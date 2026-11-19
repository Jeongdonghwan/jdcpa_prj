"""Phase 2~8 완료 기준 검증. 시드 + 콤보 생성이 끝난 DB가 필요하다.

    python seed.py --reset && flask --app app jobs combos && python tests/test_phases.py

테스트가 만든 데이터는 각 테스트가 되돌린다. 외부 호출(Claude, IndexNow, 리드 전달)은 가짜로 바꿔서 검증한다.
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_phase1 import app, client, get  # noqa: E402

import conv  # noqa: E402
import db  # noqa: E402
import jobs  # noqa: E402
import keywords  # noqa: E402
import pipeline  # noqa: E402
from adapters.base import NetworkAdapter  # noqa: E402
from db import ops, queries as dbq  # noqa: E402
from engine import gate, lint, select_offers  # noqa: E402

SECRET = "dev-postback-secret"
BUPYEONG = "/개인회생/지역/인천-부평구/"


def ctx():
    return app.app_context()


def admin_client():
    c = app.test_client()
    c.post("/admin/login", data={"user": app.config["ADMIN_USER"], "password": app.config["ADMIN_PASSWORD"]})
    with c.session_transaction() as s:
        return c, s["csrf"]


def new_click(url="/개인회생/지역/인천/", slug="demo-law-a"):
    with ctx():
        combo = dbq.combo_by_url(1, url)
    client.get(f"/go/{slug}?p={combo['id']}&s=cards")
    with ctx():
        return db.q1("SELECT * FROM clicks ORDER BY id DESC LIMIT 1")


def fake_page(tag):
    """서로 겹치지 않는 문장으로 된 가짜 생성 결과."""
    words = " ".join(f"{tag}{i}" for i in range(60))
    return json.dumps({"h1_candidates": [f"{tag} 안내"], "meta_description": f"{tag} 설명",
                       "blocks": [{"slot": "summary", "html": f"<ul><li>{tag} 요약</li></ul>"}, {"slot": "intro", "html": f"<p>{words}</p>"},
                                  {"slot": "body", "html": f"<h2>{tag}</h2><p>법원이 사안별로 판단하는 경우가 있습니다.</p>"}],
                       "faqs": [{"q": f"{tag} 질문", "a": "확인이 필요합니다."}]}, ensure_ascii=False)


# ── Phase 2 ───────────────────────────────────────────────────────────────
def test_p2_scores_and_gate_blocks_similar_pages():
    with ctx():
        site, combo = dbq.site(), dbq.combo_by_url(1, BUPYEONG)
        assert combo["status"] == "review"  # 지역명만 바꾼 시드 문장은 형제 유사도 초과로 공개되지 않는다
        content = dbq.content(combo["id"], combo["content_version"])
        assert float(content["uniqueness_score"]) > 0.35 and any("uniqueness" in f for f in gate(site, combo))
        # 공개된 형제 페이지 전수: 유사도 0.35 이하, 린트 위반 0
        rows = db.q("""SELECT k.url_path, p.uniqueness_score u, p.lint_result FROM keyword_combos k
                       JOIN page_contents p ON p.combo_id=k.id AND p.version=k.content_version WHERE k.site_id=1 AND k.status='published'""")
        bad = [r["url_path"] for r in rows if r["u"] is None or float(r["u"]) > 0.35 or not json.loads(r["lint_result"])["ok"]]
        assert len(rows) == 34 and bad == [], bad
    assert get(BUPYEONG).status_code == 404


def test_p2_lint_and_fact_flags():
    with ctx():
        v = dbq.vertical(1)
        assert lint(v, "서류를 내면 됩니다. 신청하시면 됩니다")["advice"] and not lint(v, "확인이 필요합니다")["advice"]
        assert lint(v, "업계 1위 사무소")["forbidden"] == ["1위"]
        moving = db.q1("SELECT * FROM verticals WHERE slug='이사청소'")
        assert "최저가" in lint(moving, "최저가 이사")["forbidden"] and "최저가" not in lint(v, "최저가 이사")["forbidden"]
        facts = dbq.topic_facts(1, None)
        assert pipeline.fact_flags("변제 기간은 3년이고 7년까지 가능하며 20억 원 한도", facts) == ["20억 원", "7년"]


def test_p2_generate_review_approve_publish():
    urls = [BUPYEONG, "/개인회생/지역/인천-계양구/", "/개인회생/지역/인천-남동구/"]
    with ctx():
        site = dbq.site()
        combos = [dbq.combo_by_url(1, u) for u in urls]
        ids = [c["id"] for c in combos]
        calls = []

        def fake(model, system, prompt):
            calls.append((model, prompt))
            tag = re.search(r"인천광역시 (\S+구)", prompt).group(1)
            return fake_page(tag) if len(calls) != 1 else fake_page(tag).replace("확인이 필요합니다", "무조건 됩니다")

        try:
            pipeline.AUTO_APPROVE_PERCENT, saved = 0, pipeline.AUTO_APPROVE_PERCENT
            assert [pipeline.generate(site, c, fake) for c in combos] == ["review"] * 3
            assert len(calls) == 4 and calls[0][0] == "claude-haiku-4-5" and "금지 표현" in calls[1][1]   # 린트 실패 → 피드백 넣어 재생성
            assert "인천지방법원" in calls[0][1] and "부평동" in calls[0][1]                              # 프롬프트에 실데이터 주입
            versions = db.q("SELECT * FROM page_contents WHERE combo_id=%s ORDER BY version", (ids[0],))
            assert [v["status"] for v in versions] == ["approved", "lint_fail", "review"]
            assert float(versions[2]["uniqueness_score"]) <= 0.35
            assert dbq.combo(ids[0])["status"] == "review" and get(BUPYEONG).status_code == 404         # 검수 전에는 공개 안 됨
            # 관리자 검수 화면 → 승인 → 공개 실행
            c, csrf = admin_client()
            assert "부평구 안내" in c.get("/admin/review").get_data(as_text=True)
            page = c.get(f"/admin/review/{versions[2]['id']}").get_data(as_text=True)
            assert "부평구 안내" in page and "통과" in page
            assert c.post(f"/admin/review/{versions[1]['id']}", data={"csrf": csrf, "action": "approve"}).status_code == 400  # 린트 미통과는 승인 불가
            for cid in ids:
                latest = ops.latest_content(cid)
                assert c.post(f"/admin/review/{latest['id']}", data={"csrf": csrf, "action": "approve"}).status_code == 302
            assert dbq.combo(ids[0])["status"] == "approved"
            assert pipeline.publish_ready(site, cap=0) == [] and get(BUPYEONG).status_code == 404       # 주간 상한 0 → 공개 없음
            assert "공개 3건" in c.post("/admin/review", data={"csrf": csrf}).get_data(as_text=True)
            html = get(BUPYEONG).get_data(as_text=True)
            assert "부평구 안내" in html and "인근 지역" in html and "작성자/editor" in html              # 새 버전 + 인접 지역 메시 + 작성자
            assert db.q1("SELECT COUNT(*) n FROM indexnow_queue WHERE site_id=1 AND reason='publish'")["n"] == 3
            assert pipeline.gate_audit(site) == {}
        finally:
            pipeline.AUTO_APPROVE_PERCENT = saved
            for cid in ids:
                db.ex("DELETE FROM page_contents WHERE combo_id=%s AND version>1", (cid,))
                db.ex("UPDATE keyword_combos SET status='review', content_version=1, published_at=NULL WHERE id=%s", (cid,))
            db.ex("DELETE FROM indexnow_queue")
            db.ex("DELETE FROM audit_log")
            dbq.bump_cache_version(1)


def test_p2_queue_dir_roundtrip():
    """클라우드 루틴 연동: export-prompts → (루틴이 JSON 작성) → submit-dir."""
    import shutil
    from click.testing import CliRunner
    runner, env = CliRunner(), {"PYTHONIOENCODING": "utf-8"}
    with ctx():
        combo = dbq.combo_by_url(1, BUPYEONG)
    for d in (jobs.QUEUE_DIR, jobs.ROOT / "content" / "done"):
        shutil.rmtree(d, ignore_errors=True)
    try:
        with app.app_context():
            r = runner.invoke(jobs.export_prompts, ["--limit", "30", "--status", "review"], env=env)
            assert r.exit_code == 0 and "22개 프롬프트" in r.output, r.output
            files = sorted(jobs.QUEUE_DIR.glob("*.prompt.md"))
            assert len(files) == 22 and f"<!-- url: {combo['url_path']} -->" in (jobs.QUEUE_DIR / f"{combo['id']}.prompt.md").read_text("utf-8")
            assert "0개 프롬프트" in runner.invoke(jobs.export_prompts, ["--limit", "30"], env=env).output   # 이미 나간 건 다시 안 나감
            data = json.loads(fake_page("큐테스트"))
            (jobs.QUEUE_DIR / f"{combo['id']}.json").write_text(json.dumps({"url": combo["url_path"], **data}, ensure_ascii=False), "utf-8")
            (jobs.QUEUE_DIR / "bad.json").write_text(json.dumps({"url": "/없음/", **data}), "utf-8")
            r = runner.invoke(jobs.submit_dir, [], env=env)
            assert "→ review" in r.output and "bad.json 실패" in r.output, r.output
            assert not (jobs.QUEUE_DIR / f"{combo['id']}.json").exists() and (jobs.ROOT / "content" / "done" / f"{combo['id']}.prompt.md").exists()
            latest = ops.latest_content(combo["id"])
            assert latest["model"] == "claude-routine" and latest["status"] == "review" and "큐테스트" in latest["h1"]
    finally:
        with ctx():
            db.ex("DELETE FROM page_contents WHERE combo_id=%s AND version>1", (combo["id"],))
            db.ex("UPDATE keyword_combos SET content_version=1 WHERE id=%s", (combo["id"],))
        for d in (jobs.QUEUE_DIR, jobs.ROOT / "content" / "done"):
            shutil.rmtree(d, ignore_errors=True)


def test_p2_hub_uses_sonnet_and_is_never_auto_approved():
    with ctx():
        site, combo = dbq.site(), dbq.combo_by_url(1, "/개인회생/면책/")
        models = []
        try:
            pipeline.AUTO_APPROVE_PERCENT, saved = 100, pipeline.AUTO_APPROVE_PERCENT
            status = pipeline.generate(site, combo, lambda m, s, p: models.append(m) or fake_page("면책허브"))
            assert status == "review" and models == ["claude-sonnet-5-5"]
            assert dbq.combo(combo["id"])["status"] == "published" and dbq.combo(combo["id"])["content_version"] == 1  # 공개본은 그대로
        finally:
            pipeline.AUTO_APPROVE_PERCENT = saved
            db.ex("DELETE FROM page_contents WHERE combo_id=%s AND version>1", (combo["id"],))


def test_p2_region_data_import():
    with ctx():
        try:
            out = jobs.run_import("facts", "path_slug,key,value,source,as_of\n인천-부평구,인구,테스트값,테스트출처,2026-01-01\n", dbq.site())
            assert out == "1행 반영" and dbq.region_facts(db.q1("SELECT id FROM regions WHERE path_slug='인천-부평구'")["id"])[0]["value"] == "테스트값"
        finally:
            db.ex("DELETE FROM region_facts WHERE source='테스트출처'")  # 시드의 지역 실데이터는 남긴다


# ── Phase 3 ───────────────────────────────────────────────────────────────
def test_p3_postback_dedup_orphan_and_rejection():
    click = new_click()
    url = f"/postback/demo-net?sub1={click['click_id']}&txid=T1&amount=30000&status=0"
    try:
        r = client.get(url + "&key=wrong")
        assert r.status_code == 403
        with ctx():
            assert db.q1("SELECT COUNT(*) n FROM audit_log WHERE action='postback_rejected'")["n"] == 1
            assert SECRET not in db.q1("SELECT diff FROM audit_log WHERE action='postback_rejected'")["diff"]
        assert client.get(url + f"&key={SECRET}").get_data(as_text=True) == "created"
        assert client.post(url + f"&key={SECRET}").get_data(as_text=True) == "duplicate"       # 중복 전송
        assert client.get(url.replace("status=0", "status=1") + f"&key={SECRET}").get_data(as_text=True) == "updated"
        assert client.get(f"/postback/demo-net?sub1=NOPE&txid=T2&key={SECRET}").get_data(as_text=True) == "orphan"
        assert client.get("/postback/no-such-network").status_code == 404
        with ctx():
            rows = db.q("SELECT * FROM conversions")
            assert len(rows) == 1 and rows[0]["status"] == "approved" and rows[0]["offer_id"] == click["offer_id"] and float(rows[0]["amount"]) == 30000
            assert db.q1("SELECT COUNT(*) n FROM conversions_orphan")["n"] == 1
            assert db.q1("SELECT COUNT(*) n FROM audit_log WHERE action='conversion_status'")["n"] == 1  # 상태 이력
            # 롤업 → 대시보드 EPC
            ops.rollup(1, db.q1("SELECT CURDATE() d")["d"])
            row = db.q1("SELECT SUM(clicks) c, SUM(conv_approved) a, SUM(revenue) r FROM stats_daily WHERE offer_id=%s", (click["offer_id"],))
            assert row["c"] >= 1 and row["a"] == 1 and float(row["r"]) == 30000
        c, _csrf = admin_client()
        page = c.get("/admin/dashboard").get_data(as_text=True)
        assert "임시 법률사무소 A" in page and "EPC" in page and "30,000" in page
        for g in ("combo", "region", "slot"):
            assert c.get(f"/admin/dashboard?group={g}").status_code == 200
    finally:
        with ctx():
            for t in ("conversions", "conversions_orphan", "stats_daily", "audit_log"):
                db.ex(f"DELETE FROM {t}")


def test_p3_csv_import():
    click = new_click()
    c, csrf = admin_client()
    try:
        import io
        data = f"sub1,txid,status,amount,date\n{click['click_id']},C1,승인,25000,2026-10-01 10:00:00\nUNKNOWN,C2,대기,0,\n"
        r = c.post("/admin/imports", data={"csrf": csrf, "kind": "conversions", "arg": "demo-net", "file": (io.BytesIO(data.encode()), "a.csv")},
                   content_type="multipart/form-data")
        assert "created" in r.get_data(as_text=True) and "orphan" in r.get_data(as_text=True)
        with ctx():
            row = db.q1("SELECT * FROM conversions")
            assert row["source"] == "csv" and row["status"] == "approved" and str(row["occurred_at"]).startswith("2026-10-01")
    finally:
        with ctx():
            for t in ("conversions", "conversions_orphan", "audit_log"):
                db.ex(f"DELETE FROM {t}")


def test_p3_rerank_caps_placements():
    click = new_click()
    with ctx():
        site, combo = dbq.site(), dbq.combo_by_url(1, "/개인회생/지역/인천/")
        a = dbq.offer_by_slug("demo-law-a")
        try:
            # 캡: 오늘 전환 1건 + daily_cap 1 → 스냅샷에 캡 도달로 기록 → 노출 제외
            conv.record_conversion(dbq.network(a["network_id"]), NetworkAdapter.event_from(click["click_id"], "X", "approved", "100", "lead", {}), "manual")
            db.ex("UPDATE offers SET daily_cap=1 WHERE id=%s", (a["id"],))
            version = site["ranking_version"]
            offers, capped = conv.rerank(site)
            site = dbq.site()
            assert capped == [a["id"]] and site["ranking_version"] == version + 1 and offers[str(a["id"])]["epc"] > 0
            assert "demo-law-a" not in [o["slug"] for o in select_offers(site, combo, "cards", 8)]
            db.ex("UPDATE offers SET daily_cap=NULL WHERE id=%s", (a["id"],))
            conv.rerank(site)
            # 플레이스먼트: block 은 제거, pin 은 맨 앞
            b, c3 = dbq.offer_by_slug("demo-law-b"), dbq.offer_by_slug("demo-law-c")
            db.ex("INSERT INTO offer_placements (offer_id, scope_type, scope_id, slot, mode) VALUES (%s,'vertical',1,'cards','block')", (b["id"],))
            db.ex("INSERT INTO offer_placements (offer_id, scope_type, scope_id, slot, mode, position) VALUES (%s,'combo',%s,'cards','pin',1)", (c3["id"], combo["id"]))
            slugs = [o["slug"] for o in select_offers(dbq.site(), combo, "cards", 8)]
            assert slugs[0] == "demo-law-c" and "demo-law-b" not in slugs
        finally:
            for t in ("offer_placements", "conversions", "audit_log"):
                db.ex(f"DELETE FROM {t}")
            db.ex("UPDATE offers SET daily_cap=NULL")
            conv.rerank(dbq.site())


def test_p3_ab_test_and_impressions():
    with ctx():
        combo, b = dbq.combo_by_url(1, "/개인회생/"), dbq.offer_by_slug("demo-law-b")
        db.ex("DELETE FROM stats_daily")
        db.ex("""INSERT INTO ab_tests (name, scope_type, scope_id, slot, arms, status) VALUES ('t','combo',%s,'form',%s,'running')""",
              (combo["id"], json.dumps([{"variant": "b1", "offer_id": b["id"]}, {"variant": "b2", "offer_id": b["id"]}])))
    try:
        c = app.test_client()
        r = c.get("/%EA%B0%9C%EC%9D%B8%ED%9A%8C%EC%83%9D/", headers={"User-Agent": "Mozilla/5.0"})
        html = r.get_data(as_text=True)
        variant = re.search(r"/go/demo-law-b\?p=\d+&amp;s=form&amp;ab=(b[12])", html).group(1)
        assert "v=" in r.headers["Set-Cookie"] and "no-store" in r.headers["Cache-Control"]
        assert variant in c.get("/%EA%B0%9C%EC%9D%B8%ED%9A%8C%EC%83%9D/", headers={"User-Agent": "Mozilla/5.0"}).get_data(as_text=True)  # 같은 방문자 = 같은 arm
        assert c.get(f"/go/demo-law-b?p={combo['id']}&s=form&ab={variant}").status_code == 302
        client.get("/%EA%B0%9C%EC%9D%B8%ED%9A%8C%EC%83%9D/", headers={"User-Agent": "Yeti/1.1"})  # 봇은 노출 집계 제외
        with ctx():
            assert db.q1("SELECT variant FROM clicks ORDER BY id DESC LIMIT 1")["variant"] == variant
            imp = db.q1("SELECT SUM(impressions) n FROM stats_daily WHERE slot='form' AND variant=%s AND offer_id=%s", (variant, b["id"]))["n"]
            assert imp == 2, imp
    finally:
        with ctx():
            db.ex("DELETE FROM ab_tests")
            db.ex("DELETE FROM stats_daily")


def test_p3_integration_modes_and_second_network_by_config_only():
    c, csrf = admin_client()
    try:
        # 두 번째 네트워크·오퍼를 관리자 입력만으로 등록(코드 변경 0줄) → 포스트백까지 동작
        net = {"csrf": csrf, "slug": "net-two", "name": "두번째", "kind": "direct_advertiser", "is_active": "1",
               "postback": json.dumps({"enabled": True, "click_id_param": "cid", "external_id_param": "oid", "status_param": "st",
                                       "secret_param": "token", "secret": "s2", "status_map": {"approved": "ok"}})}
        assert c.post("/admin/t/networks/new", data=net).status_code == 302
        with ctx():
            nid = ops.network_by_slug("net-two")["id"]
        base = {"csrf": csrf, "network_id": nid, "vertical_id": 1, "advertiser": "모드 테스트", "name": "m", "payout_type": "CPA", "status": "active",
                "compliance_fields": '{"ad_lawyer": "정임시"}', "manual_weight": 1}
        for slug, extra in {"m-phone": {"integration_mode": "phone", "phone": "032-000-0000"},
                            "m-kakao": {"integration_mode": "kakao", "kakao_url": "https://pf.kakao.com/_x/chat?ref={click_id}"},
                            "m-iframe": {"integration_mode": "iframe", "embed_url_template": "https://example.com/frm?code=Z&sub={click_id}"},
                            "m-own": {"integration_mode": "own_form"}}.items():
            assert c.post("/admin/t/offers/new", data={**base, "slug": slug, **extra}).status_code == 302, slug
        assert client.get("/go/m-phone").headers["Location"] == "tel:0320000000"
        assert client.get("/go/m-kakao").headers["Location"].startswith("https://pf.kakao.com/_x/chat?ref=")
        loc = client.get("/go/m-iframe").headers["Location"]
        assert loc.startswith("https://example.com/frm?code=Z&sub=") and "{" not in loc
        with ctx():
            click_id = db.q1("SELECT click_id FROM clicks ORDER BY id DESC LIMIT 1")["click_id"]
            # iframe 오퍼를 폼 슬롯 기본값으로 → 페이지에 /go/ 경유 iframe 출력
            db.ex("UPDATE verticals SET form_offer_id=%s WHERE id=1", (dbq.offer_by_slug("m-iframe")["id"],))
            dbq.bump_cache_version(1)
            assert "m-own" not in [o["slug"] for o in select_offers(dbq.site(), dbq.combo_by_url(1, "/개인회생/"), "cards", 8)]  # 플래그 꺼짐 → 자체 폼 오퍼 비노출
        html = get("/개인회생/").get_data(as_text=True)
        assert re.search(r'<iframe src="/go/m-iframe\?p=\d+&amp;s=form" loading="lazy" title="모드 테스트 상담 신청"', html)
        assert client.get(f"/postback/net-two?cid={click_id}&oid=9&st=ok&token=s2").get_data(as_text=True) == "created"
        with ctx():
            assert db.q1("SELECT status, network_id FROM conversions") == {"status": "approved", "network_id": nid}
    finally:
        with ctx():
            a = dbq.offer_by_slug("demo-law-a")["id"]
            db.ex("UPDATE verticals SET form_offer_id=%s WHERE id=1", (a,))
            db.ex("DELETE FROM conversions")
            db.ex("DELETE FROM offers WHERE slug LIKE 'm-%%'")
            db.ex("DELETE FROM networks WHERE slug='net-two'")
            dbq.bump_cache_version(1)


# ── Phase 4 ───────────────────────────────────────────────────────────────
def test_p4_parse_and_combo_space():
    with ctx():
        v = dbq.vertical(1)
        sp = keywords.Space(v)
        pt, d = sp.classify(sp.parse("인천 개인파산 비용"))
        assert pt == "region_topic_intent" and d["region"]["path_slug"] == "인천" and d["intent"]["term"] == "비용"
        pt, d = sp.classify(sp.parse("인천 개인회생 비용"))                              # topic "개인회생 비용" 이 먼저 잡힌다(같은 URL, 같은 의도)
        assert pt == "region_topic" and d["topic"]["term"] == "개인회생 비용"
        assert sp.classify(sp.parse("주식빚개인회생"))[0] == "topic_situation"          # 붙여 쓴 동의어
        assert sp.classify(sp.parse("부평구 개인파산"))[1]["region"]["path_slug"] == "인천-부평구"
        assert sp.classify(sp.parse("공무원 코인 빚"))[0] == "situation_persona"
        assert sp.parse("개인회생 맛집") is None and sp.parse("개인회생 개인파산") is None
        assert sp.classify(sp.parse("인천 주식 빚 개인회생 비용"))[0] is None           # 지역×상황×수식어 3중 조합 금지
        topic_hub = next(x for x in sp.specs() if x[0] == "topic_intent")
        assert sp.independent(*topic_hub, volume=0) is False and sp.independent(*topic_hub, volume=30) is True
        try:
            ops.set_setting(1, "require_volume", "0")
            assert keywords.Space(v).independent(*topic_hub, volume=0) is True            # 검색량 없이 운영하는 선택지
        finally:
            ops.set_setting(1, "require_volume", "1")
        total = db.q1("SELECT COUNT(*) n, SUM(priority_score>0) scored FROM keyword_combos")
        assert total["n"] > 2000 and total["scored"] > 2000
        assert ops.group_rep_violations(1) == []                                        # 카니벌 그룹 대표 1개 규칙
        for row in db.q("SELECT page_type, url_path FROM keyword_combos WHERE id IN (SELECT MIN(id) FROM keyword_combos GROUP BY page_type)"):
            assert row["url_path"].startswith("/개인회생/") and row["url_path"].endswith("/") and "--" not in row["url_path"]
        urls = {r["url_path"] for r in db.q("SELECT url_path FROM keyword_combos WHERE url_path LIKE '/개인회생/상황/%%' LIMIT 3")}
        assert urls


def test_p4_ingest_promotes_and_merged_redirects():
    with ctx():
        site, v = dbq.site(), dbq.vertical(1)
        target = dbq.combo_by_url(1, "/개인회생/개인파산-비용/")
        assert target["status"] == "merged"
    assert unquote(get("/개인회생/개인파산-수임료/").headers["Location"]) == "/개인회생/개인파산/"  # 변형 → 대표(merged) → 공개된 조상으로 바로 301
    try:
        with ctx():
            rows = [{"keyword": "개인파산 비용", "pc": 100, "mobile": 400, "competition": 0.8}, {"keyword": "개인파산수임료", "pc": 10, "mobile": 40},
                    {"keyword": "개인회생 맛집", "pc": 1, "mobile": 1}, {"keyword": "면책", "pc": 7, "mobile": 9}]
            assert keywords.ingest(site, v, rows) == {"matched": 3, "unparsed": 1}
            assert dbq.combo(target["id"])["volume_total"] == 550                       # 동의어 검색량은 대표에 합산
            assert db.q1("SELECT volume_pc, volume_mobile FROM keyword_terms WHERE term='면책' AND vertical_id=1") == {"volume_pc": 7, "volume_mobile": 9}
            assert json.loads(ops.queued("kw_review")[0]["payload"])["keyword"] == "개인회생 맛집"
            keywords.rebuild(site, v)
            promoted = dbq.combo(target["id"])
            assert promoted["status"] == "draft" and float(promoted["priority_score"]) > 6  # 검색량 임계값 통과 → 독립 페이지 후보
            assert dbq.combo_by_url(1, "/개인회생/개인파산-수임료/")["status"] == "merged"
            assert ops.group_rep_violations(1) == []
        c, csrf = admin_client()
        assert "개인회생 맛집" in c.get("/admin/kw_review").get_data(as_text=True)
    finally:
        with ctx():
            db.ex("UPDATE keyword_combos SET volume_total=0, competition=NULL WHERE id=%s", (target["id"],))
            db.ex("UPDATE keyword_terms SET volume_pc=NULL, volume_mobile=NULL")
            db.ex("DELETE FROM job_queue")
            keywords.rebuild(dbq.site(), dbq.vertical(1))
            assert dbq.combo(target["id"])["status"] == "merged"


def test_p4_legacy_flat_url():
    r = get("/인천-법률/")
    assert r.status_code == 301 and unquote(r.headers["Location"]) == "/개인회생/지역/인천/"
    assert get("/인천-이사청소/").status_code == 404 and get("/없는곳-법률/").status_code == 404


def test_p4_naver_api_needs_keys():
    try:
        keywords.fetch_naver(["개인회생"])
    except RuntimeError as e:
        assert "NAVER_AD_API_KEY" in str(e)
    else:
        raise AssertionError("키 없이 호출되면 안 된다")


# ── Phase 5 ───────────────────────────────────────────────────────────────
def test_p5_og_indexnow_sitemap_anchors():
    html = get("/개인회생/지역/인천/").get_data(as_text=True)
    m = re.search(r'<meta property="og:image" content="http://localhost:5000(/og/c/\d+\.webp)\?v=\w+">', html)  # 페이지별 카드(og.py)
    r = client.get(m.group(1)) if m else None
    assert r and r.status_code == 200 and r.mimetype == "image/webp" and "summary_large_image" in html
    with ctx():
        target = dbq.combo_by_url(1, "/개인회생/개인파산/")
        anchors = ops.anchor_variants([target["id"]])[target["id"]]
        assert len(anchors) == 3
    seen = set()
    for r in dbq_published():
        seen |= set(re.findall(r'<a href="/개인회생/개인파산/">([^<]+)</a>', get(r["url_path"]).get_data(as_text=True)))
    assert len(seen & set(anchors)) >= 2, seen                                           # 출발 페이지마다 다른 앵커
    sm = get("/사이트맵/").get_data(as_text=True)
    assert "변제금 계산기" in sm and "/개인회생/사이트맵/지역/" in sm
    assert "인천 개인파산" in get("/개인회생/사이트맵/지역/").get_data(as_text=True)
    # IndexNow: 키 파일 + 큐 전송 로그(외부 호출은 가짜)
    with ctx():
        db.ex("UPDATE sites SET indexnow_key='abc123key'")
        db.ex("DELETE FROM indexnow_queue")
        dbq.queue_indexnow(1, "https://example.com/a/", "publish")
        sent = []
        try:
            assert client.get("/abc123key.txt").get_data(as_text=True) == "abc123key" and client.get("/other.txt").status_code == 404
            try:
                jobs.indexnow_send(dbq.site())
                raise AssertionError("로컬 주소로는 보내지 않아야 한다")
            except ValueError:
                pass
            app.config["BASE_URL"] = "https://example.com"
            n, code = jobs.indexnow_send(dbq.site(), lambda url, payload: sent.append((url, payload)) or 200)
            assert (n, code) == (1, 200) and sent[0][1]["keyLocation"] == "https://example.com/abc123key.txt" and sent[0][1]["urlList"] == ["https://example.com/a/"]
            row = db.q1("SELECT sent_at, response_code FROM indexnow_queue")
            assert row["sent_at"] and row["response_code"] == 200
        finally:
            app.config["BASE_URL"] = "http://localhost:5000"
            db.ex("UPDATE sites SET indexnow_key=NULL")
            db.ex("DELETE FROM indexnow_queue")


def dbq_published():
    with ctx():
        return dbq.published(1)


def test_p5_tools():
    url = "/개인회생/도구/변제금-계산기/"
    page = get(url).get_data(as_text=True)
    assert "index,follow" in page and "복지로" in page and "1,538,542원" in page          # 2,564,238 × 60%
    r = get(url + "?income=3000000&household=1").get_data(as_text=True)
    assert "1,461,458원" in r and "52,612,488원" in r and 'content="noindex,follow"' in r  # (3,000,000−1,538,542) × 36
    assert f'<link rel="canonical" href="http://localhost:5000/%EA%B0%9C%EC%9D%B8%ED%9A%8C%EC%83%9D/' in r and "?" not in re.search(r'canonical" href="([^"]+)"', r).group(1)
    assert "변제계획을 세우기 어려울 수" in get(url + "?income=1000000&household=4").get_data(as_text=True)
    check = "/개인회생/도구/자가진단/"
    assert get(check).status_code == 200
    assert "개인회생 제도" in get(check + "?done=1&a=1&b=1&c=1").get_data(as_text=True)
    assert "개인파산 제도" in get(check + "?done=1&c=1&d=1&e=1").get_data(as_text=True)
    assert get("/이사청소/도구/자가진단/").status_code == 404 and get("/개인회생/도구/없음/").status_code == 404


# ── Phase 6 ───────────────────────────────────────────────────────────────
_LOGDAY = __import__('datetime').date.today()  # 주간 리포트가 최근 7일만 세므로 실행일 기준
LOG = '''66.249.66.1 - - [{LOGDAY}:10:00:00 +0900] "GET /%EA%B0%9C%EC%9D%B8%ED%9A%8C%EC%83%9D/?x=1 HTTP/1.1" 200 5123 "-" "Mozilla/5.0 (compatible; Googlebot/2.1)"
110.93.1.1 - - [{LOGDAY}:10:00:01 +0900] "GET /%EA%B0%9C%EC%9D%B8%ED%9A%8C%EC%83%9D/ HTTP/1.1" 200 5123 "-" "Mozilla/5.0 (compatible; Yeti/1.1; +https://naver.me/spd)"
110.93.1.1 - - [{LOGDAY}:10:00:02 +0900] "GET /none/ HTTP/1.1" 404 300 "-" "Mozilla/5.0 (compatible; Yeti/1.1; +https://naver.me/spd)"
1.2.3.4 - - [{LOGDAY}:10:00:03 +0900] "GET / HTTP/1.1" 200 100 "-" "Mozilla/5.0 Chrome/120"
'''.replace('{LOGDAY}', _LOGDAY.strftime('%d/%b/%Y'))


def test_p6_imports_logs_report():
    with ctx():
        site = dbq.site()
        try:
            page = "http://localhost:5000/개인회생/지역/인천/"
            gsc = f"date,page,query,device,clicks,impressions,ctr,position\n{db.q1('SELECT CURDATE() d')['d']},{page},인천 개인회생,MOBILE,3,120,0.025,8.2\n"
            assert jobs.run_import("gsc", gsc, site) == "1행 적재" and jobs.run_import("gsc", gsc, site) == "1행 적재"  # 다시 올려도 중복 없음
            assert db.q1("SELECT COUNT(*) n FROM gsc_daily")["n"] == 1
            assert jobs.run_import("naver", "week,metric,dim,value\n2026-09-28,수집,전체,31\n", site) == "1행 적재"
            assert jobs.run_import("rank", "date,combo_id,engine,keyword,position,url\n2026-10-05,1,naver,개인회생,14,\n", site) == "1행 적재"
            try:
                jobs.run_import("gsc", "a,b\n1,2\n", site)
                raise AssertionError("열 검증 실패")
            except ValueError:
                pass
            hits = jobs.parse_log(LOG.splitlines())
            day = _LOGDAY.isoformat()
            assert hits == {(day, "/개인회생/", "google", 200): 1, (day, "/개인회생/", "naver", 200): 1, (day, "/none/", "naver", 404): 1}
            ops.add_bot_hits(hits)
            report = jobs.weekly_report(site)
            for text in ("공개 페이지: 34개", "인천 개인회생: 클릭 3, 노출 120", "naver: 2회 (오류 응답 1회)", "제목 테스트"):
                assert text in report, text
        finally:
            for t in ("gsc_daily", "naver_sa_weekly", "rank_daily", "bot_crawl_daily"):
                db.ex(f"DELETE FROM {t}")


def test_p6_prune_flow_and_title_test():
    url = "/개인회생/지역/인천-면책/"
    with ctx():
        site, combo = dbq.site(), dbq.combo_by_url(1, url)
        assert jobs.find_prune_candidates(site) == 0                                    # 공개한 지 120일이 안 됨
        db.ex("UPDATE keyword_combos SET published_at=NOW()-INTERVAL 130 DAY WHERE id=%s", (combo["id"],))
        assert jobs.find_prune_candidates(site) == 1 and jobs.find_prune_candidates(site) == 0  # 큐에 중복 적재 없음
    c, csrf = admin_client()
    try:
        assert url in c.get("/admin/queue").get_data(as_text=True)
        with ctx():
            job_id = ops.queued("prune")[0]["id"]
        assert get(url).status_code == 200                                              # 승인 전에는 그대로
        assert c.post("/admin/queue", data={"csrf": csrf, "id": job_id, "action": "approve"}).status_code == 302
        r = get(url)
        assert r.status_code == 301 and unquote(r.headers["Location"]) == "/개인회생/면책/"
        with ctx():
            assert ops.queued("prune") == [] and db.q1("SELECT COUNT(*) n FROM audit_log WHERE action='prune_approve'")["n"] == 1
            # 제목 테스트: 기간 중에는 변형 제목·설명으로 렌더
            hub = dbq.combo_by_url(1, "/개인회생/면책/")
            db.ex("""INSERT INTO title_tests (combo_id, variant_title, variant_desc, start_at, end_at, status)
                     VALUES (%s,'면책 테스트 제목','면책 테스트 설명',CURDATE(),CURDATE(),'running')""", (hub["id"],))
            dbq.bump_cache_version(1)
        html = get("/개인회생/면책/").get_data(as_text=True)
        assert "<title>면책 테스트 제목</title>" in html and 'content="면책 테스트 설명"' in html and html.count("<h1>") == 1
    finally:
        with ctx():
            db.ex("UPDATE keyword_combos SET status='published', published_at=NOW() WHERE url_path=%s", (url,))
            for t in ("job_queue", "title_tests", "audit_log"):
                db.ex(f"DELETE FROM {t}")
            dbq.bump_cache_version(1)


# ── Phase 7 ───────────────────────────────────────────────────────────────
def test_p7_verticals_stay_closed_without_offers():
    home = get("/").get_data(as_text=True)
    assert "이사·청소" not in home and "/장기렌트/" not in home
    assert get("/이사청소/").status_code == 404 and get("/이사청소/rss.xml").status_code == 404
    rss = get("/개인회생/rss.xml")
    assert rss.status_code == 200 and rss.get_data(as_text=True).count("<item>") == 20
    c, csrf = admin_client()
    with ctx():
        v = db.q1("SELECT * FROM verticals WHERE slug='이사청소'")
        assert len(ops.terms(v["id"])) == 17 and "biz_name" in v["compliance_schema"]
    form = {k: ("" if v[k] is None else v[k]) for k in ("site_id", "slug", "name", "url_base", "compliance_schema")}
    r = c.post(f"/admin/t/verticals/{v['id']}", data={**form, "csrf": csrf, "is_active": "1"})
    assert "오퍼가 1개 이상" in r.get_data(as_text=True)                                  # 오퍼 없이는 활성화 불가
    with ctx():
        assert db.q1("SELECT is_active FROM verticals WHERE id=%s", (v["id"],))["is_active"] == 0


# ── Phase 8 ───────────────────────────────────────────────────────────────
def test_p8_own_form_disabled_by_default():
    assert app.config["OWN_FORM_ENABLED"] is False
    with ctx():
        db.ex("""INSERT INTO offers (network_id, vertical_id, slug, advertiser, name, payout_type, integration_mode, compliance_fields, compliance_ok, status)
                 VALUES (1,1,'own-x','자체폼 상담처','n','CPL','own_form','{"ad_lawyer":"한임시"}',1,'active')""")
    try:
        assert client.get("/lead/own-x").status_code == 404 and client.post("/lead/own-x", data={"name": "a"}).status_code == 404
        with ctx():
            assert db.q1("SELECT COUNT(*) n FROM leads")["n"] == 0
    finally:
        with ctx():
            db.ex("DELETE FROM offers WHERE slug='own-x'")


def test_p8_own_form_flow_when_enabled():
    from cryptography.fernet import Fernet
    forwarded = []
    saved_forward = NetworkAdapter.forward_lead
    with ctx():
        db.ex("""INSERT INTO offers (network_id, vertical_id, slug, advertiser, name, payout_type, integration_mode, compliance_fields, compliance_ok, status, forward_config)
                 VALUES (1,1,'own-x','자체폼 상담처','n','CPL','own_form','{"ad_lawyer":"한임시"}',1,'active','{"method":"webhook","url":"https://example.com/hook"}')""")
    app.config.update(OWN_FORM_ENABLED=True, LEAD_KEY=Fernet.generate_key().decode())
    NetworkAdapter.forward_lead = lambda self, offer, lead: forwarded.append(lead) or {"ok": True, "status": 200}
    try:
        c = app.test_client()
        assert c.get("/lead/own-x").status_code == 404                                   # 동의문이 없으면 폼을 내보내지 않는다
        with ctx():
            ops.set_setting(1, "consent_collect_text", "수집 항목: 이름, 연락처. 목적: 상담 연결. 보유: 90일.")
            ops.set_setting(1, "consent_third_party_text", "제공받는 자: {advertiser}. 항목: 이름, 연락처. 목적: 상담. 보유: 90일.")
            ops.set_setting(1, "consent_version", "v-test")
            dbq.bump_cache_version(1)
        r = c.get("/go/own-x")                                                            # 카드 클릭 → 클릭 로그 → 폼으로
        assert r.status_code == 302 and r.headers["Location"].startswith("/lead/own-x?c=")
        page = c.get(r.headers["Location"])
        html = page.get_data(as_text=True)
        assert "제공받는 자: 자체폼 상담처" in html and "v-test" in html and "no-store" in page.headers["Cache-Control"] and "noindex" in html
        csrf, click_id = re.search(r'name="csrf" value="(\w+)"', html).group(1), re.search(r'name="c" value="(\w+)"', html).group(1)
        good = {"csrf": csrf, "c": click_id, "name": "홍길동", "phone": "010-1234-5678", "consent_collect": "1", "consent_third_party": "1"}
        assert c.post("/lead/own-x", data={**good, "csrf": "bad"}).status_code == 400
        assert "모두 필요합니다" in c.post("/lead/own-x", data={k: v for k, v in good.items() if k != "consent_third_party"}).get_data(as_text=True)
        assert "연락처를 숫자로" in c.post("/lead/own-x", data={**good, "phone": "12"}).get_data(as_text=True)
        with ctx():
            assert db.q1("SELECT COUNT(*) n FROM leads")["n"] == 0                       # 동의·검증 실패는 저장 안 함
        assert "접수되었습니다" in c.post("/lead/own-x", data=good).get_data(as_text=True)
        with ctx():
            lead = db.q1("SELECT * FROM leads")
            assert lead["click_id"] == click_id and lead["consent_collect"] == 1 and lead["consent_third_party"] == 1 and lead["consent_version"] == "v-test"
            assert b"01012345678" not in lead["phone_enc"] and Fernet(app.config["LEAD_KEY"].encode()).decrypt(lead["phone_enc"]) == b"01012345678"
            assert forwarded[0]["phone"] == "01012345678" and json.loads(lead["forward_result"])["ok"] and lead["forwarded_at"]
            assert (lead["retention_until"] - lead["created_at"].date()).days == 90
        ac, _csrf = admin_client()
        listing = ac.get("/admin/leads").get_data(as_text=True)
        assert "홍**" in listing and "*******5678" in listing and "01012345678" not in listing  # 마스킹 + 열람 기록
        with ctx():
            assert db.q1("SELECT COUNT(*) n FROM audit_log WHERE action='leads_view'")["n"] == 1
            assert ops.purge_leads() == 0
            db.ex("UPDATE leads SET retention_until=CURDATE()-INTERVAL 1 DAY")
            assert ops.purge_leads() == 1                                                 # 보유기간 경과 → 파기
            lead = db.q1("SELECT * FROM leads")
            assert lead["name_enc"] is None and lead["phone_enc"] is None and lead["purged_at"]
        assert "자체 상담 신청서를 이용하는 경우" in get("/개인정보처리방침/").get_data(as_text=True)
    finally:
        NetworkAdapter.forward_lead = saved_forward
        app.config.update(OWN_FORM_ENABLED=False, LEAD_KEY="")
        with ctx():
            for t in ("leads", "audit_log"):
                db.ex(f"DELETE FROM {t}")
            db.ex("DELETE FROM offers WHERE slug='own-x'")
            db.ex("DELETE FROM site_settings WHERE `key` LIKE 'consent%%'")


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_p"):
            try:
                fn()
                print("PASS", name)
            except Exception as e:  # noqa: BLE001
                failed += 1
                import traceback
                print("FAIL", name, "→", type(e).__name__, e)
                traceback.print_exc(limit=3)
    sys.exit(1 if failed else 0)
