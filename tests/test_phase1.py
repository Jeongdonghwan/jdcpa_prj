"""Phase 1 완료 기준 검증. 시드된 DB가 필요하다(python seed.py --reset).

    python tests/test_phase1.py        # pytest 로도 실행 가능
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from app import create_app  # noqa: E402
from db import queries as dbq  # noqa: E402
from engine import gate, lint_text, select_offers  # noqa: E402

app = create_app()
client = app.test_client()
LD_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
A_RE = re.compile(r"<a\b[^>]*>", re.S)


def get(path, **kw):
    return client.get(quote(path, safe="/?=&"), **kw)


def published():
    with app.app_context():
        return dbq.published(1)


def local_path(url):
    return unquote(urlsplit(url).path)


def test_published_combos_200():
    rows = published()
    assert len(rows) >= 34
    for r in rows:
        assert get(r["url_path"]).status_code == 200, r["url_path"]
    assert get("/").status_code == 200


def test_unknown_url_404():
    for path in ["/없는페이지/", "/개인회생/지역/없는곳/", "/개인회생/지역/인천-없는구/", "/go/no-such-offer", "/sitemap-region-9.xml"]:
        assert get(path).status_code == 404, path
    with app.app_context():  # draft 콤보는 URL이 있어도 404
        db.ex("UPDATE keyword_combos SET status='draft' WHERE url_path='/개인회생/면책/'")
    try:
        assert get("/개인회생/면책/").status_code == 404
    finally:
        with app.app_context():
            db.ex("UPDATE keyword_combos SET status='published' WHERE url_path='/개인회생/면책/'")


def test_url_normalization_301():
    for src, dst in [("/개인회생", "/개인회생/"), ("/개인회생/지역/인천--부평구/", "/개인회생/지역/인천-부평구/"), ("/운영사", "/운영사/")]:
        r = get(src)
        assert r.status_code == 301 and unquote(r.headers["Location"]) == dst, (src, r.status_code, r.headers.get("Location"))


def test_merged_combo_redirects_to_parent():
    with app.app_context():
        db.ex("UPDATE keyword_combos SET status='merged' WHERE url_path='/개인회생/지역/인천-면책/'")
    try:
        r = get("/개인회생/지역/인천-면책/")
        assert r.status_code == 301 and unquote(r.headers["Location"]) == "/개인회생/면책/"
    finally:
        with app.app_context():
            db.ex("UPDATE keyword_combos SET status='published' WHERE url_path='/개인회생/지역/인천-면책/'")


def test_go_redirect_logs_one_click():
    with app.app_context():
        combo = dbq.combo_by_url(1, "/개인회생/지역/인천/")
        before = db.q1("SELECT COUNT(*) n FROM clicks")["n"]
    for i in range(3):
        r = client.get(f"/go/demo-law-a?p={combo['id']}&s=cards")
        assert r.status_code == 302 and r.headers["Cache-Control"] == "no-store"
        with app.app_context():
            assert db.q1("SELECT COUNT(*) n FROM clicks")["n"] == before + i + 1
            last = db.q1("SELECT * FROM clicks ORDER BY id DESC LIMIT 1")
        assert last["combo_id"] == combo["id"] and last["slot"] == "cards" and last["region_id"] == combo["region_id"]
        loc = r.headers["Location"]
        assert loc.startswith("https://example.com/pt/demo-law-a?sub1=" + last["click_id"]), loc
        assert "sub2=" + quote("인천") + "&" in loc and "{" not in loc


def test_offer_order_deterministic():
    with app.app_context():
        site = dbq.site()
        orders = set()
        for c in published():
            combo = dbq.combo(c["id"])
            a = [o["slug"] for o in select_offers(site, combo, "cards", 8)]
            assert a == [o["slug"] for o in select_offers(site, combo, "cards", 8)]
            orders.add(tuple(a))
        assert len(orders) > 1, "페이지마다 순서가 달라야 한다"
        db.ex("UPDATE sites SET cache_version=cache_version+1")  # 캐시를 버리고 다시 렌더해도 HTML 동일
    url = "/개인회생/지역/인천-개인파산/"
    first = get(url).data
    with app.app_context():
        db.ex("UPDATE sites SET cache_version=cache_version+1")
    assert get(url).data == first


def test_noncompliant_offer_never_shown_and_ad_links():
    for r in published():
        html = get(r["url_path"]).get_data(as_text=True)
        assert "demo-law-nolawyer" not in html and "표기 누락" not in html, r["url_path"]
        go_links = [a for a in A_RE.findall(html) if 'href="/go/' in a]
        assert go_links, r["url_path"]
        for a in go_links:
            assert 'rel="sponsored nofollow noopener"' in a and 'target="_blank"' in a, a
        for a in A_RE.findall(html):  # 외부 링크는 /go/ 가 아니면 법원·법령 출처뿐
            href = re.search(r'href="([^"]+)"', a).group(1)
            assert href.startswith(("/", "#")) or "scourt.go.kr" in href or "law.go.kr" in href, href  # "#": 글 안 목차 링크
    assert client.get("/go/demo-law-nolawyer").status_code == 302  # 비노출 오퍼 직접 호출 → fallback 오퍼로
    assert "demo-law-a" in client.get("/go/demo-law-nolawyer").headers["Location"]


def test_jsonld_valid_and_matches_screen():
    for r in published():
        html = get(r["url_path"]).get_data(as_text=True)
        docs = [json.loads(m) for m in LD_RE.findall(html)]  # 유효한 JSON 이어야 한다
        types = [d.get("@type") or [g["@type"] for g in d["@graph"]] for d in docs]
        assert types[:3] == [["Organization", "WebSite"], "BreadcrumbList", "FAQPage"] and types[3:] in ([], ["ItemList"]), (r["url_path"], types)
        if types[3:]:  # 관련 페이지 목록: 화면의 링크와 같아야 한다
            for it in docs[3]["itemListElement"]:
                assert f'href="{unquote(urlsplit(it["url"]).path)}"' in html and it["name"] in html
        crumbs, faq = docs[1]["itemListElement"], docs[2]["mainEntity"]
        assert [c["position"] for c in crumbs] == list(range(1, len(crumbs) + 1))
        assert unquote(urlsplit(crumbs[-1]["item"]).path) == r["url_path"]
        for c in crumbs:
            assert c["name"] in html
        for f in faq:  # 화면에 보이는 Q/A 와 일치
            assert f"<summary>{f['name']}</summary>" in html and f["acceptedAnswer"]["text"] in html


def test_robots_sitemap_rss():
    robots = client.get("/robots.txt").get_data(as_text=True)
    for line in ["Disallow: /go/", "Disallow: /lead/", "Disallow: /postback/", "Sitemap: http://localhost:5000/sitemap-index.xml"]:
        assert line in robots, line
    index = client.get("/sitemap-index.xml").get_data(as_text=True)
    urls = []
    for sm in re.findall(r"<loc>(.*?)</loc>", index):
        body = client.get(local_path(sm)).get_data(as_text=True)
        urls += re.findall(r"<loc>(.*?)</loc>", body)
    assert len(urls) == len(set(urls)) >= 34 + 1 + 6 + 4
    for u in urls:
        assert "%" in u or u.endswith(("/", ".xml")), u
        assert get(local_path(u)).status_code == 200, u
    rss = client.get("/rss.xml").get_data(as_text=True)
    assert rss.count("<item>") == 20


def test_canonical_and_meta():
    seen = set()
    for r in published():
        html = get(r["url_path"]).get_data(as_text=True)
        assert f'<link rel="canonical" href="http://localhost:5000{quote(r["url_path"])}">' in html
        title = re.search(r"<title>(.*?)</title>", html).group(1)
        assert title not in seen and html.count("<h1>") == 1
        seen.add(title)
    assert 'content="noindex,follow"' in get("/없는페이지/").get_data(as_text=True)


def test_gate_and_lint():
    assert lint_text("결과 보장합니다") and not lint_text("법원이 판단합니다")
    with app.app_context():
        site = dbq.site()
        combo = dbq.combo_by_url(1, "/개인회생/지역/인천/")
        assert gate(site, combo) == []
        db.ex("UPDATE offers SET status='paused'")  # 오퍼가 없으면 게이트 실패
        try:
            assert any("오퍼" in f for f in gate(site, combo))
        finally:
            db.ex("UPDATE offers SET status='active'")


def test_admin_requires_login_and_saves():
    c = app.test_client()
    assert c.get("/admin/t/offers/").status_code == 302
    assert c.post("/admin/login", data={"user": "admin", "password": "wrong"}).status_code == 200
    r = c.post("/admin/login", data={"user": app.config["ADMIN_USER"], "password": app.config["ADMIN_PASSWORD"]})
    assert r.status_code == 302
    for t in ("networks", "offers", "regions"):
        assert c.get(f"/admin/t/{t}/").status_code == 200
    with c.session_transaction() as s:
        csrf = s["csrf"]
    form = {"network_id": 1, "vertical_id": 1, "slug": "test-offer", "advertiser": "테스트 상담처", "name": "테스트", "payout_type": "CPA",
            "integration_mode": "redirect", "tracking_url_template": "https://example.com/t?c={click_id}", "status": "active",
            "compliance_fields": "{}"}
    assert c.post("/admin/t/offers/new", data=form).status_code == 400  # CSRF 토큰 없음
    assert "금지 표현" in c.post("/admin/t/offers/new", data={**form, "csrf": csrf, "blurb": "업계 1위"}).get_data(as_text=True)
    try:
        assert c.post("/admin/t/offers/new", data={**form, "csrf": csrf}).status_code == 302
        with app.app_context():
            row = dbq.offer_by_slug("test-offer")
            assert row["compliance_ok"] == 0  # 광고책임변호사 미기재 → 비노출
            assert db.q1("SELECT COUNT(*) n FROM audit_log WHERE target=%s", (f"offers:{row['id']}",))["n"] == 1
        c.post(f"/admin/t/offers/{row['id']}", data={**form, "csrf": csrf, "compliance_fields": '{"ad_lawyer": "최임시"}'})
        with app.app_context():
            assert dbq.offer_by_slug("test-offer")["compliance_ok"] == 1
        region = {"court_address": "테스트 주소"}
        with app.app_context():
            rid = db.q1("SELECT id FROM regions WHERE path_slug='인천'")["id"]
            r1 = dbq.region(rid)
        data = {k: ("" if v is None else v) for k, v in r1.items() if k in ("parent_id", "level", "code", "slug", "short_name", "full_name",
                "path_slug", "path_name", "court_name", "court_url", "publish_stage", "sort_order")}
        assert c.post(f"/admin/t/regions/{rid}", data={**data, **region, "csrf": csrf}).status_code == 302
        assert "테스트 주소" in get("/개인회생/지역/인천/").get_data(as_text=True)  # 저장 → 캐시 무효화 → 페이지 반영
    finally:
        with app.app_context():
            db.ex("DELETE FROM offers WHERE slug='test-offer'")
            db.ex("UPDATE regions SET court_address=NULL WHERE path_slug='인천'")
            db.ex("UPDATE sites SET cache_version=cache_version+1")


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("PASS", name)
            except Exception as e:  # noqa: BLE001
                failed += 1
                print("FAIL", name, "→", type(e).__name__, e)
    sys.exit(1 if failed else 0)
