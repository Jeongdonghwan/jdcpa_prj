"""배치 잡: `flask --app app jobs <name>`. cron 에서 호출한다(스펙 6.2). 요청 처리 경로에서는 쓰지 않는다."""
import csv
import io
import json
import os
import re
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

import click
from flask import current_app, g
from flask.cli import AppGroup

import conv
import keywords
import pipeline
import db
from db import ops, queries as dbq
from engine import abs_url, site_base

jobs_cli = AppGroup("jobs", help="배치 잡")
ROOT = Path(__file__).parent
BOTS = {"Yeti": "naver", "Googlebot": "google", "Daum": "daum", "bingbot": "bing"}
LOG_RE = re.compile(r'\S+ \S+ \S+ \[(\d{2}/\w{3}/\d{4}):[^\]]+\] "(?:GET|HEAD) (\S+)[^"]*" (\d{3}) \S+ "[^"]*" "([^"]*)"')


def _sites(only=None):
    """대상 사이트들. only = 서브도메인 첫 라벨(law 등) 또는 id. 사이트마다 기준 주소(g.base_url)를 맞춘다."""
    for s in dbq.sites():
        if only and str(s["id"]) != str(only) and s["host"].split(".")[0] != only:
            continue
        g.base_url = None if s["id"] == 1 else f"https://{s['host']}"
        yield s


SITE_OPT = click.option("--site", "only", default=None, help="사이트(서브도메인 첫 라벨 또는 id). 생략하면 전체")


def _verticals(site):
    return dbq.verticals(site["id"])


# ── Phase 2: 콘텐츠 ───────────────────────────────────────────────────────
@jobs_cli.command("quality")
@SITE_OPT
def quality(only):
    """전 콤보 재채점(린트·고유성·팩트) 후, 공개 중인데 게이트에 걸리는 콤보를 review 로 내린다."""
    for site in _sites(only):
        click.echo(f"[{site['host']}] 채점 {pipeline.score_all(site['id'])}건")
        for url, fails in pipeline.gate_audit(site).items():
            click.echo(f"  공개 중단: {url} {fails}")


@jobs_cli.command("generate")
@click.option("--limit", default=20, help="이번 실행에서 생성할 콤보 수")
@click.option("--status", default="draft", help="대상 콤보 상태(draft | review)")
def generate(limit, status):
    """Claude 로 콘텐츠 생성(ANTHROPIC_API_KEY 필요, 과금됨)."""
    site, done = dbq.site(), 0
    for combo in ops.combos_by_status(site["id"], [status]):
        if done >= limit:
            break
        try:
            click.echo(f"{combo['url_path']} → {pipeline.generate(site, combo)}")
        except Exception as e:  # 한 건 실패가 배치를 멈추지 않게 하되 사유는 남긴다
            click.echo(f"{combo['url_path']} 실패: {type(e).__name__}: {e}")
        done += 1


@jobs_cli.command("prompt")
@click.argument("url")
def prompt(url):
    """콤보의 생성 프롬프트를 출력한다(Claude Code 세션에서 직접 쓸 때)."""
    combo = dbq.combo_by_url(dbq.site()["id"], url) or (_ for _ in ()).throw(click.ClickException("없는 URL"))
    click.echo(pipeline.build_prompt(combo))


@jobs_cli.command("submit")
@click.argument("url")
@click.argument("path", type=click.Path(exists=True))
def submit(url, path):
    """직접 쓴 생성 결과(JSON 파일)를 콤보에 넣고 채점·검수 큐로 보낸다. API 과금 없음."""
    site = dbq.site()
    combo = dbq.combo_by_url(site["id"], url) or (_ for _ in ()).throw(click.ClickException("없는 URL"))
    status, uniq, result = pipeline.submit(site, combo, Path(path).read_text("utf-8-sig"))
    click.echo(f"{url} → {status} (유사도 {uniq}, 린트 {'통과' if result['ok'] else result})")


QUEUE_DIR = ROOT / "content" / "queue"   # 클라우드 루틴과 git 으로 주고받는 폴더: *.prompt.md(서버→루틴), *.json(루틴→서버)


@jobs_cli.command("export-prompts")
@click.option("--limit", default=10, help="내보낼 콤보 수(루틴 1회 분량)")
@click.option("--status", default="review", help="대상 콤보 상태(review | draft)")
def export_prompts(limit, status):
    """콘텐츠가 필요한 콤보의 프롬프트를 content/queue/<id>.prompt.md 로 내보낸다(클라우드 루틴이 읽어 JSON 을 쓴다)."""
    site, n = dbq.site(), 0
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    pending = {p.stem.split(".")[0] for p in QUEUE_DIR.glob("*.prompt.md")} | {p.stem for p in QUEUE_DIR.glob("*.json")}
    for combo in ops.combos_by_status(site["id"], [status]):
        if n >= limit:
            break
        if str(combo["id"]) in pending or (combo["content_version"] and ops.latest_content(combo["id"])["status"] in ("review", "lint_fail")):
            continue  # 이미 나가 있거나 검수 대기 중인 콤보는 건너뛴다
        (QUEUE_DIR / f"{combo['id']}.prompt.md").write_text(
            f"<!-- url: {combo['url_path']} -->" + chr(10)
            + f"<!-- 결과는 같은 폴더에 {combo['id']}.json 으로 저장: 아래 '출력 형식'의 JSON 객체에 url 키를 더해서 -->" + chr(10) * 2
            + pipeline.build_prompt(combo), "utf-8")
        n += 1
    click.echo(f"{n}개 프롬프트 → {QUEUE_DIR}")


@jobs_cli.command("submit-dir")
def submit_dir():
    """content/queue/*.json 을 콤보에 넣고(채점·검수 큐) 처리한 파일은 content/done/ 로 옮긴다. 서버 cron: git pull 뒤 실행."""
    site, done_dir = dbq.site(), ROOT / "content" / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(QUEUE_DIR.glob("*.json")):
        text = path.read_text("utf-8-sig")
        try:
            url = json.loads(text)["url"]
            combo = dbq.combo_by_url(site["id"], url) or (_ for _ in ()).throw(ValueError(f"없는 URL {url}"))
            status, uniq, result = pipeline.submit(site, combo, text, model="claude-routine")
            click.echo(f"{path.name} {url} → {status} (유사도 {uniq}, 린트 {'통과' if result['ok'] else '미통과'})")
        except (ValueError, KeyError) as e:
            click.echo(f"{path.name} 실패: {e}")
            continue
        path.rename(done_dir / path.name)
        prompt = QUEUE_DIR / f"{path.stem}.prompt.md"
        if prompt.exists():
            prompt.rename(done_dir / prompt.name)


@jobs_cli.command("publish")
@SITE_OPT
@click.option("--quiet", is_flag=True, help="건별 출력 없이 합계만")
def publish(only, quiet):
    """approved 콤보를 게이트 검사 후 공개. 상한: publish_plan(날짜별 하루 수) 또는 weekly_publish_cap."""
    for site in _sites(only):
        rows = pipeline.publish_ready(site)
        if not quiet:
            for url, fails in rows:
                click.echo(f"{url}: {'공개' if not fails else fails}")
        click.echo(f"[{site['host']}] 공개 {sum(1 for _u, f in rows if not f)} · 보류 {sum(1 for _u, f in rows if f)} · 상태 {ops.count_status(site['id'])}")


@jobs_cli.command("plan")
@click.argument("start")
@SITE_OPT
@click.option("--days", default=5, help="처음 나눠 여는 기간(일)")
@click.option("--then", "then", default=500, help="그 뒤 하루 공개 수(나머지 페이지)")
@click.option("--min-priority", default=60, help="처음 DAYS 일에 여는 범위: 우선순위가 이 값 이상인 페이지(60 = 동 대표 주제까지)")
def plan(start, only, days, then, min_priority):
    """공개 계획 저장: START(YYYY-MM-DD)부터 DAYS 일 동안 우선순위 MIN_PRIORITY 이상 비공개 페이지를 고르게 나눠 열고,
    이후 나머지를 하루 THEN 개씩. publish 는 우선순위 높은 순으로 연다."""
    from datetime import timedelta
    until = (date.fromisoformat(start) + timedelta(days=days - 1)).isoformat()
    for site in _sites(only):
        if site["id"] == 1:
            continue
        first = db.q1("SELECT COUNT(*) n FROM keyword_combos WHERE site_id=%s AND status='approved' AND priority_score>=%s", (site["id"], min_priority))["n"]
        rest = ops.count_status(site["id"]).get("approved", 0) - first
        per_day = -(-first // days) if first else then
        ops.set_setting(site["id"], "publish_plan", json.dumps([{"until": until, "per_day": per_day}, {"per_day": then}]))
        click.echo(f"[{site['host']}] {start}~{until} 하루 {per_day}쪽(1차 {first}쪽), 이후 하루 {then}쪽(나머지 {rest}쪽, 약 {-(-rest // then) if then else 0}일)")


@jobs_cli.command("stage")
@SITE_OPT
@click.option("--yes", is_flag=True, help="확인 없이 실행")
def stage(only, yes):
    """오픈 준비: 공개 페이지를 모두 approved 로 되돌린다(허브는 남김). 이후 publish 가 publish_plan 대로 연다."""
    if not yes:
        click.confirm("공개 페이지를 비공개(approved)로 되돌립니다. 계속할까요?", abort=True)
    for site in _sites(only):
        if site["id"] == 1:
            continue
        ops.stage_all(site["id"], ("vertical_hub", "topic_hub"))
        dbq.bump_cache_version(site["id"])
        click.echo(f"[{site['host']}] 비공개로 전환 · 상태 {ops.count_status(site['id'])}")


def run_import(kind, text, site, arg=None):
    """CSV 적재 공통 진입점(CLI·관리자 업로드). 결과 요약 문자열을 돌려준다."""
    text = text.lstrip("\ufeff")
    rows = list(csv.DictReader(io.StringIO(text)))
    if kind in ops.IMPORTS:  # gsc | naver | rank
        missing = set(ops.IMPORTS[kind][1][:2]) - set(rows[0] if rows else [])
        if missing:
            raise ValueError(f"CSV 열이 없습니다: {', '.join(missing)} (필요: {', '.join(ops.IMPORTS[kind][1])})")
        return f"{ops.import_rows(kind, site['id'], rows)}행 적재"
    if kind == "conversions":
        network = ops.network_by_slug(arg or "")
        if not network:
            raise ValueError("네트워크 slug 가 필요합니다")
        return str(conv.import_csv(network, text))
    if kind == "keywords":  # keyword,pc,mobile,competition
        out = {v["slug"]: keywords.ingest(site, v, rows) for v in _verticals(site) if not arg or v["slug"] == arg}
        return f"{out} → 배치 잡 combos 로 콤보 상태를 다시 계산하세요"
    if kind == "court":     # path_slug,court_name,court_branch,court_address,court_phone,court_url
        for r in rows:
            ops.update_region_court(r["path_slug"], r)
    elif kind == "facts":   # path_slug,key,value,source,as_of
        for r in rows:
            ops.upsert_region_fact(r["path_slug"], r)
    else:
        raise ValueError(f"알 수 없는 종류: {kind}")
    dbq.bump_cache_version(site["id"])
    return f"{len(rows)}행 반영"


@jobs_cli.command("import")
@click.argument("kind")
@click.argument("path", type=click.Path(exists=True))
@click.option("--arg", default=None, help="conversions: 네트워크 slug / keywords: 버티컬 slug")
def import_cmd(kind, path, arg):
    """CSV 적재: gsc | naver | rank | conversions | keywords | court | facts"""
    click.echo(run_import(kind, Path(path).read_text("utf-8-sig"), dbq.site(), arg))


# ── Phase 3: 전환·통계 ────────────────────────────────────────────────────
@jobs_cli.command("rollup")
@click.option("--day", default=None, help="YYYY-MM-DD (기본: 오늘)")
def rollup(day):
    """clicks·conversions → stats_daily(전 사이트)."""
    for site in _sites():
        ops.rollup(site["id"], day or date.today().isoformat())


@jobs_cli.command("rerank")
def rerank():
    """EPC·캡 스냅샷 갱신 + ranking_version 증가(전 사이트)."""
    for site in _sites():
        offers, capped = conv.rerank(site)
        click.echo(f"[{site['host']}] 오퍼 {len(offers)}개, 캡 도달 {capped}")


# ── Phase 4: 키워드 ───────────────────────────────────────────────────────
@jobs_cli.command("kw-fetch")
@click.option("--limit", default=50, help="조회할 시드 용어 수(호출 제한 고려)")
def kw_fetch(limit):
    """네이버 검색광고 API로 시드 용어의 연관 키워드·검색량 수집."""
    site = dbq.site()
    for v in _verticals(site):
        seeds = [t["term"] for t in ops.terms(v["id"]) if t["dim"] == "topic"][:limit]
        for i in range(0, len(seeds), 5):
            try:
                click.echo(f"{seeds[i:i + 5]} → {keywords.ingest(site, v, keywords.fetch_naver(seeds[i:i + 5]))}")
            except RuntimeError as e:
                raise click.ClickException(str(e))


@jobs_cli.command("combos")
def combos():
    """콤보 생성·상태 재계산(8.4 규칙) + 카니벌 그룹 대표 검증."""
    site = dbq.site()
    for v in _verticals(site):
        click.echo(f"{v['slug']}: {keywords.rebuild(site, v)}")
        bad = ops.group_rep_violations(v["id"])
        click.echo(f"  카니벌 그룹 대표 규칙 위반 {len(bad)}건" + (f": {bad[:5]}" if bad else ""))


# ── Phase 5: SEO 확장 ─────────────────────────────────────────────────────
@jobs_cli.command("og-images")
def og_images():
    """공개 콤보의 OG 이미지 생성: /static/og/{combo_id}.webp (1200x630)."""
    from PIL import Image, ImageDraw, ImageFont
    font_path = os.environ.get("OG_FONT", r"C:\Windows\Fonts\malgunbd.ttf")
    if not Path(font_path).exists():
        raise click.ClickException(f"한글 폰트가 없습니다: {font_path} (환경변수 OG_FONT 로 지정)")
    site, big, small, n = dbq.site(), ImageFont.truetype(font_path, 64), ImageFont.truetype(font_path, 32), 0
    (ROOT / "static" / "og").mkdir(parents=True, exist_ok=True)
    for c in dbq.published(site["id"]):
        out = ROOT / "static" / "og" / f"{c['id']}.webp"
        content = dbq.content(c["id"], dbq.combo(c["id"])["content_version"])
        if not content:
            continue
        if out.exists():
            ops.set_og_image(c["id"], f"/static/og/{c['id']}.webp")
            continue
        img = Image.new("RGB", (1200, 630), "#0b4fb3")
        draw, lines, line = ImageDraw.Draw(img), [], ""
        for word in content["h1"].split():
            if line and draw.textlength(f"{line} {word}", font=big) > 1040:
                lines.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        draw.multiline_text((80, 200), "\n".join((lines + [line])[:4]), font=big, fill="white", spacing=18)
        draw.text((80, 80), site["name"], font=small, fill="#cfe0ff")
        img.save(out, "WEBP", quality=82)
        ops.set_og_image(c["id"], f"/static/og/{c['id']}.webp")
        n += 1
    if n:
        dbq.bump_cache_version(site["id"])
    click.echo(f"OG 이미지 {n}개 생성")


CORE_TYPES = ("vertical_hub", "topic_hub")
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def _urlset(rows):
    import html as _h
    body = "".join(f"<url><loc>{_h.escape(abs_url(r['url_path']))}</loc>"
                   + (f"<lastmod>{r['content_updated_at']:%Y-%m-%d}</lastmod>" if r.get("content_updated_at") else "") + "</url>\n" for r in rows)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="{SITEMAP_NS}">\n{body}</urlset>\n'


@jobs_cli.command("export-sitemaps")
@click.argument("out", type=click.Path())
@SITE_OPT
@click.option("--chunk", default=1000, help="키워드 사이트맵 1개당 주소 수")
def export_sitemaps(out, only, chunk):
    """사이트별 정적 사이트맵: OUT/<첫 라벨>/sitemap.xml(색인)·sitemap-core.xml·sitemap-kw-N.xml + .ops.json.
    nginx 가 이 폴더를 root 로 두고 /sitemap*.xml 을 직접 내준다(SEO 대시보드가 같은 파일을 읽는다). lastmod = content_updated_at."""
    from engine import STATIC_PATHS
    for site in _sites(only):
        if site["id"] == 1:
            continue
        d = Path(out) / site["host"].split(".")[0]
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("sitemap*.xml"):
            old.unlink()
        rows = dbq.published(site["id"], None, 10_000_000)
        core = [{"url_path": p} for p in ["/", *STATIC_PATHS, "/사이트맵/"]] + [r for r in rows if r["page_type"] in CORE_TYPES or r.get("url_path", "").count("-") == 0 and "/지역/" in r["url_path"]]
        core_ids = {r.get("id") for r in core}
        kw = [r for r in rows if r["id"] not in core_ids]
        (d / "sitemap-core.xml").write_text(_urlset(core), "utf-8")
        files = ["sitemap-core.xml"]
        for i in range(0, len(kw), chunk):
            name = f"sitemap-kw-{i // chunk + 1}.xml"
            (d / name).write_text(_urlset(kw[i:i + chunk]), "utf-8")
            files.append(name)
        idx = "".join(f"<sitemap><loc>{abs_url('/' + f)}</loc></sitemap>\n" for f in files)
        (d / "sitemap.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="{SITEMAP_NS}">\n{idx}</sitemapindex>\n', "utf-8")
        status = ops.count_status(site["id"])
        plan = json.loads(dbq.settings(site["id"]).get("publish_plan") or "[]")
        sched, left, day = [], status.get("approved", 0), date.today()
        from datetime import timedelta
        while left > 0 and plan and len(sched) < 3650:  # 남은 공개 일정(대시보드 '앞으로 열릴 수')
            day += timedelta(days=1)
            step = next((p for p in plan if not p.get("until") or day.isoformat() <= p["until"]), plan[-1])
            n = min(int(step["per_day"]), left)
            sched.append({"date": day.isoformat(), "pages": n})
            left -= n
        (d / ".ops.json").write_text(json.dumps({"total_pages": sum(status.values()), "published": status.get("published", 0),
                                                 "schedule": sched[:60]}, ensure_ascii=False), "utf-8")
        click.echo(f"[{site['host']}] 공개 {len(rows)} → {d} ({len(files)}개 파일)")


@jobs_cli.command("og-topics")
@SITE_OPT
def og_topics(only):
    """서브도메인 사이트: 업종 주제별 OG 이미지(1200x630) 를 만들고 그 주제의 모든 페이지에 연결한다(페이지별 생성은 수십만 장이라 하지 않는다)."""
    from PIL import Image, ImageDraw, ImageFont
    font_path = os.environ.get("OG_FONT", r"C:\Windows\Fonts\malgunbd.ttf")
    if not Path(font_path).exists():
        raise click.ClickException(f"한글 폰트가 없습니다: {font_path} (환경변수 OG_FONT 로 지정)")
    big, small = ImageFont.truetype(font_path, 70), ImageFont.truetype(font_path, 32)
    (ROOT / "static" / "og").mkdir(parents=True, exist_ok=True)
    for site in _sites(only):
        if site["id"] == 1:
            continue
        th = json.loads(dbq.settings(site["id"]).get("theme") or "{}")
        for v in _verticals(site):
            for t in ops.terms(v["id"]):
                if t["dimension_id"] != 1:
                    continue
                name = f"t{t['id']}.webp"
                img = Image.new("RGB", (1200, 630), "white")
                dr = ImageDraw.Draw(img)
                dr.rectangle([0, 0, 1200, 630], fill=th.get("tint", "#eef2f6"))
                dr.rectangle([0, 600, 1200, 630], fill=th.get("accent", "#0e7c86"))
                dr.text((90, 110), v["name"], font=small, fill=th.get("accent", "#0e7c86"))
                lines, line = [], ""
                for word in t["term"].split():
                    if line and dr.textlength(f"{line} {word}", font=big) > 1000:
                        lines.append(line)
                        line = word
                    else:
                        line = f"{line} {word}".strip()
                dr.multiline_text((90, 190), "\n".join((lines + [line])[:3]), font=big, fill=th.get("ink", "#15202b"), spacing=16)
                dr.text((90, 500), site["name"], font=small, fill=th.get("muted", "#5c6b7a"))
                img.save(ROOT / "static" / "og" / name, "WEBP", quality=82)
                ops.set_topic_og(t["id"], f"/static/og/{name}")
        dbq.bump_cache_version(site["id"])
        click.echo(f"[{site['host']}] 주제 OG 연결 완료")


def post_json(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def indexnow_send(site, post=post_json):
    """큐의 URL을 IndexNow 로 전송하고 sent_at·response_code 를 기록한다. (보낸 수, 응답코드)"""
    host = urlsplit(site_base()).netloc
    if not site["indexnow_key"]:
        raise ValueError("sites.indexnow_key 가 비어 있습니다")
    if host.split(":")[0] in ("localhost", "127.0.0.1"):
        raise ValueError("로컬 주소는 IndexNow 로 보내지 않습니다(BASE_URL 확인)")
    rows = ops.indexnow_pending(site["id"], int(dbq.settings(site["id"]).get("indexnow_daily_cap", 500)))
    if not rows:
        return 0, None
    code = post("https://api.indexnow.org/indexnow", {
        "host": host, "key": site["indexnow_key"], "keyLocation": abs_url(f"/{site['indexnow_key']}.txt"),
        "urlList": sorted({r["url"] for r in rows})})
    ops.indexnow_mark([r["id"] for r in rows], code)
    return len(rows), code


@jobs_cli.command("indexnow")
@SITE_OPT
def indexnow(only):
    """IndexNow 큐 전송(네이버·빙). 키가 없는 사이트는 건너뛴다."""
    for site in _sites(only):
        try:
            click.echo(f"[{site['host']}] 전송 %s건, 응답 %s" % indexnow_send(site))
        except ValueError as e:
            click.echo(f"[{site['host']}] 건너뜀: {e}")


@jobs_cli.command("anchors")
@SITE_OPT
def anchors(only):
    """공개 콤보의 앵커 텍스트 변형(3개) 생성."""
    for site in _sites(only):
        n = 0
        word = "상담처" if site["id"] <= 2 else "안내 글"
        for c in dbq.published(site["id"]):
            kw = c["primary_keyword"]
            ops.replace_anchors(c["id"], [kw, f"{kw} 안내", f"{kw} {word}"])
            n += 1
        dbq.bump_cache_version(site["id"])
        click.echo(f"[{site['host']}] {n}개 콤보")


# ── Phase 6: 측정 ─────────────────────────────────────────────────────────
def parse_log(lines):
    """nginx combined 로그 → {(date, path, bot, status): hits}. 검색엔진 봇 요청만."""
    hits = defaultdict(int)
    for line in lines:
        m = LOG_RE.match(line)
        bot = m and next((name for ua, name in BOTS.items() if ua in m.group(4)), None)
        if bot:
            day = datetime.strptime(m.group(1), "%d/%b/%Y").date().isoformat()
            hits[(day, unquote(m.group(2).split("?")[0])[:255], bot, int(m.group(3)))] += 1
    return hits


@jobs_cli.command("logs")
@click.argument("path", type=click.Path(exists=True))
def logs(path):
    """nginx access log 파싱 → bot_crawl_daily."""
    with open(path, encoding="utf-8", errors="replace") as f:
        hits = parse_log(f)
    ops.add_bot_hits(hits)
    click.echo(f"{len(hits)}행")


def find_prune_candidates(site):
    """공개 후 N일(기본 120) 지났는데 노출 < 50 이거나 클릭·전환이 0인 콤보를 정리 후보 큐에 넣는다."""
    days = int(dbq.settings(site["id"]).get("prune_after_days", 120))
    queued = {json.loads(j["payload"])["combo_id"] for j in ops.queued("prune")}
    added = 0
    for c in ops.prune_candidates(site["id"], days, current_app.config["BASE_URL"]):
        if c["id"] not in queued:
            ops.enqueue("prune", {"combo_id": c["id"], "url": c["url_path"], "keyword": c["primary_keyword"],
                                  "reason": f"노출 {c['impressions']}, 클릭 {c['gsc_clicks']}, 전환 {c['convs']}"})
            added += 1
    return added


@jobs_cli.command("prune")
def prune():
    """정리(병합) 후보 찾기. 실제 병합은 관리자 승인 후."""
    click.echo(f"정리 후보 {find_prune_candidates(dbq.site())}건 추가")


def weekly_report(site):
    ops.title_test_metrics(current_app.config["BASE_URL"])
    w = ops.weekly_numbers(site["id"])
    s = w["stats"]
    epc = float(s["revenue"]) / int(s["clicks"]) if int(s["clicks"]) else 0
    lines = [f"# 주간 리포트 {date.today()}", "",
             f"- 공개 페이지: {w['published']}개 (최근 7일 신규 {w['published_7d']}개)",
             f"- 광고 노출 {s['impressions']}, 클릭 {s['clicks']}, 승인 전환 {s['approved']}, 수익 {float(s['revenue']):,.0f}원, EPC {epc:,.1f}원",
             f"- 검수 대기 {w['review_queue']}건, 정리 후보 {w['prune_queue']}건", "", "## 유입 검색어 상위(GSC, 7일)"]
    lines += [f"- {r['query']}: 클릭 {r['clicks']}, 노출 {r['impressions']}" for r in w["queries"]] or ["- 데이터 없음"]
    lines += ["", "## 검색엔진 봇 크롤(7일)"]
    lines += [f"- {r['bot']}: {r['hits']}회 (오류 응답 {r['errors']}회)" for r in w["bots"]] or ["- 데이터 없음"]
    lines += ["", "## 제목 테스트"]
    lines += [f"- {r['url_path']} \"{r['variant_title']}\" [{r['status']}]: 노출 {r['impressions'] or 0}, 클릭 {r['clicks'] or 0}"
              for r in w["title_tests"]] or ["- 진행 중인 테스트 없음"]
    return "\n".join(lines) + "\n"


@jobs_cli.command("report")
def report():
    """주간 리포트 생성 → reports/weekly-YYYY-MM-DD.md"""
    out = ROOT / "reports" / f"weekly-{date.today()}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(weekly_report(dbq.site()), "utf-8")
    click.echo(str(out))


# ── Phase 8: 리드 ─────────────────────────────────────────────────────────
@jobs_cli.command("purge-leads")
def purge_leads():
    """보유기간이 지난 리드의 개인정보 파기."""
    n = ops.purge_leads()
    dbq.audit("job", "purge_leads", "leads", json.dumps({"count": n}))
    click.echo(f"{n}건 파기")
