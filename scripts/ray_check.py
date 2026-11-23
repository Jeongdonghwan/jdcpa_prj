"""레이 구조·h1 수식어 점검(2026-11-24, 읽기 전용 — DB 에 쓰지 않는다): python scripts/ray_check.py [--per 30] [--html 폴더]

 ① h1(키워드+수식어) 중복 — 공개된 지역 키워드 페이지(region_topic) 전부. 지역·법원·업종 자료를 한 번에 읽어 ray.h1sub 로 계산
 ② 같은 주제 페이지끼리 같은 문장 비율 — (사이트, 주제)마다 표본 per 쪽을 템플릿으로 렌더(engine.assemble + render_template,
    render_combo 를 거치지 않아 pages·노출 집계 쓰기 없음). 본문(main) 글을 화면 줄 → 문장으로 나눠 쌍마다
    A 의 문장 글자 중 B 에도 그대로 있는 비율(dup_measure 와 같은 방식). 전체 쌍 평균 · 주제별 평균 최대 · 쌍 최대.
 ③ 조사 오류 — 번호 절·요약·수식어에서 '을를' 같은 자리표시와, 확인 항목·하위 지역 이름 뒤 조사 받침 불일치
 ④ 번호 절 문단 길이 · 키워드 통째로 든 h2 · 본문 글자 · 본문 내부 링크 수
--html 폴더: 사이트마다 2쪽 렌더 결과를 저장.
"""
import argparse
import collections
import html as H
import itertools
import json
import os
import random
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ray  # noqa: E402
from app import create_app  # noqa: E402
from db import q  # noqa: E402

SENT = re.compile(r"(?<=[.!?])\s+")
PLACEHOLDER = re.compile(r"을를|를을|은는|는은|과와|와과|이나나|\(으\)로|\(이\)|\(가\)|\{[a-z]+\}")


def main_units(page):
    t = re.sub(r"<script.*?</script>|<style.*?</style>|<header.*?</header>|<footer.*?</footer>|<nav.*?</nav>|<aside.*?</aside>", " ", page, flags=re.S)
    m = re.search(r"<main[^>]*>(.*?)</main>", t, re.S)
    x = H.unescape(re.sub(r"<[^>]+>", "\n", m.group(1) if m else t))
    return [s.strip() for line in x.split("\n") for s in SENT.split(line.strip()) if len(s.strip()) > 12]


def batchim(ch):
    return (ord(ch) - 0xAC00) % 28 != 0


def josa_errors(text, words):
    errs = []
    for w in set(words):
        if not w or not ("가" <= w[-1] <= "힣"):
            continue
        for m in re.finditer(re.escape(w) + r"(은|는|을|를|과|와)(?=\s)", text):
            if (m.group(1) in "은을과") != batchim(w[-1]):
                errs.append(w + m.group(1))
    return errs + PLACEHOLDER.findall(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=30)
    ap.add_argument("--html")
    a = ap.parse_args()
    app = create_app()
    with app.app_context():
        sites = {s["id"]: s for s in q("SELECT * FROM sites")}
        verts = {v["id"]: v for v in q("SELECT * FROM verticals")}
        regions = {r["id"]: r for r in q("SELECT id, parent_id, level, short_name, court_name, court_branch FROM regions")}
        kids = collections.defaultdict(list)
        for r in q("SELECT id, parent_id FROM regions ORDER BY sort_order, id"):
            if r["parent_id"]:
                kids[r["parent_id"]].append(regions[r["id"]]["short_name"])
        rows = q("""SELECT k.id, k.site_id, k.vertical_id, k.topic_id, k.region_id, k.primary_keyword, pc.h1 FROM keyword_combos k
                    JOIN page_contents pc ON pc.combo_id=k.id AND pc.version=k.content_version
                    WHERE k.status='published' AND k.page_type='region_topic'""")
        rows = [c for c in rows if not sites[c["site_id"]]["host"].startswith("localhost")]  # 사이트 1(개발용) 제외

    def court_of(rid):
        r = regions.get(rid)
        while r and not r["court_name"]:
            r = regions.get(r["parent_id"])
        return r

    # ① h1 중복(전수)
    h1s, per_site = collections.Counter(), collections.Counter()
    for c in rows:
        v = verts[c["vertical_id"]]
        sc = json.loads(v["compliance_schema"] or "{}")
        ui = sc.get("ui") or {}
        cr = court_of(c["region_id"]) if not ui.get("no_court") else None
        court = (cr["court_name"] + (" " + cr["court_branch"] if cr["court_branch"] else "")) if cr else ""
        guide = ray.pick(sc.get("guide") or [], 1, f"{c['id']}g")
        checks = ray.pick(json.loads(v["issues"] or "[]"), 3, f"{c['id']}c")
        kw = (c["h1"] or c["primary_keyword"]).split(", ", 1)[0]
        sub = ray.h1sub(c["id"], kw, kids.get(c["region_id"], []), court, [x[0] for x in checks], guide[0][0] if guide else "", ui.get("partner") or "제휴 상담처")
        host = sites[c["site_id"]]["host"]
        per_site[host] += 1
        h1s[(host, kw + " " + sub)] += 1
    dup = [k for k, n in h1s.items() if n > 1]
    print("지역 키워드 페이지", dict(per_site), "합계", sum(per_site.values()))
    print("h1 중복", len(dup), dup[:5])

    # ②③④ 표본 렌더
    import engine
    from flask import render_template
    by = collections.defaultdict(list)
    for c in rows:
        by[(c["site_id"], c["topic_id"])].append(c["id"])
    rnd, allv, groups, errs, plens, kwh2, chars, nlinks, saved = random.Random(3), [], [], [], [], [], [], [], collections.Counter()
    for (sid, tid), ids in sorted(by.items()):
        s = sites[sid]
        sub = s["host"].split(".")[0]
        docs = []
        for cid in rnd.sample(ids, min(a.per, len(ids))):
            with app.app_context(), app.test_request_context("/", base_url=f"http://{sub}.localhost:5000"):
                combo = q("SELECT * FROM keyword_combos WHERE id=%s", (cid,))[0]
                ctx = engine.assemble(s, combo)
                page = render_template(f"pages/{combo['page_type']}.html", **ctx)
            R = ctx["ray"]
            docs.append(main_units(page))
            body = " ".join([R["h1sub"], R["summary"]] + [x["h2"] + " " + x["text"] for x in R["sections"]])
            errs += josa_errors(body, [x[0] for x in json.loads(ctx["vertical"]["issues"] or "[]")] + [r["short_name"] for r in ctx["region_children"]] + [R["kw"]])
            plens += [len(x["text"]) for x in R["sections"]]
            kwh2.append(sum(1 for x in R["sections"] if x["h2"].startswith(R["kw"])))
            chars.append(len("".join(docs[-1]).replace(" ", "")))
            art = page.split('<div class="post">', 1)[-1].split('<aside class="side"', 1)[0]
            nlinks.append(len(set(re.findall(r'href="(/[^"#?]*)"', art)) - {"/광고고지/"}))
            if a.html and saved[sid] < 2:
                os.makedirs(a.html, exist_ok=True)
                open(os.path.join(a.html, f"{sub}-{cid}.html"), "w", encoding="utf-8").write(page)
                saved[sid] += 1
        if len(docs) < 2:
            continue
        v = []
        for x, y in itertools.permutations(docs, 2):
            sy, tot = set(y), sum(map(len, x)) or 1
            v.append(sum(len(t) for t in x if t in sy) / tot)
        allv += v
        groups.append((statistics.mean(v), sites[sid]["host"].split(".")[0], tid))
    groups.sort(reverse=True)
    print("조사 오류", len(errs), collections.Counter(errs).most_common(5))
    print("같은 주제끼리 같은 문장: 쌍 평균 %.1f%% · 주제별 평균 최대 %.1f%% (%s topic %s) · 쌍 최대 %.1f%%" % (
        100 * statistics.mean(allv), 100 * groups[0][0], groups[0][1], groups[0][2], 100 * max(allv)))
    print("  주제별 평균 상위", [(h, t, round(100 * m, 1)) for m, h, t in groups[:6]])
    sp = sorted(plens)
    print("번호 절 문단 글자 평균 %d · 중앙 %d · 10%%~90%% %d~%d" % (statistics.mean(sp), statistics.median(sp), sp[len(sp) // 10], sp[len(sp) * 9 // 10]))
    print("키워드 든 h2 평균 %.2f · 본문(main) 글자 평균 %d · 본문 내부 링크 평균 %.1f (최소 %d 최대 %d)" % (
        statistics.mean(kwh2), statistics.mean(chars), statistics.mean(nlinks), min(nlinks), max(nlinks)))


if __name__ == "__main__":
    main()
