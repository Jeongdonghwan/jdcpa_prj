"""공개 페이지(web), 클릭 리다이렉터(go), 포스트백, 자체 폼(lead), robots/sitemap/rss(seo). 스펙 10.2."""
import hashlib
import hmac
import json
import re
import secrets
import uuid
from datetime import date, timedelta
from math import ceil
from urllib.parse import quote

from flask import Blueprint, Response, abort, current_app, g, redirect, render_template, request, session

import conv
from adapters.base import get_adapter
from db import js, ops, queries as dbq
from engine import abs_url, render_combo, shown_offers, site_base

web_bp = Blueprint("web", __name__)
go_bp = Blueprint("go", __name__)
postback_bp = Blueprint("postback", __name__)
lead_bp = Blueprint("lead", __name__)
seo_bp = Blueprint("seo", __name__)

STATIC_PAGES = {  # url → (템플릿, 제목)
    "/운영사/": ("operator", "운영사 정보"),
    "/광고고지/": ("ad-disclosure", "광고 고지"),
    "/편집기준/": ("editorial", "편집 기준"),
    "/개인정보처리방침/": ("privacy", "개인정보처리방침"),
    "/이용약관/": ("terms", "이용약관"),
    "/면책조항/": ("disclaimer", "면책조항"),
}
TOOLS = {"변제금-계산기": ("repayment", "개인회생 변제금 계산기"), "자가진단": ("selfcheck", "개인회생·파산 자가진단")}
TOOL_VERTICAL = "/개인회생"
LEGACY_SERVICES = {"법률": "/개인회생"}  # 참조 사이트형 평탄 URL(/인천-부평구-법률)의 서비스 접미 → 버티컬
SITEMAP_TYPES = {
    "hub": ("vertical_hub", "topic_hub"), "region": ("region_topic", "region_topic_intent", "court_alias"),
    "topic": ("topic_intent", "topic_situation", "topic_persona", "situation_persona"),
}
SITEMAP_CHUNK = 5000
BOT_RE = re.compile(r"bot|crawl|spider|yeti|slurp|curl|wget|python-requests|headless|lighthouse", re.I)
PHONE_RE = re.compile(r"^0\d{8,10}$")
B62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


LOCAL_HOST = re.compile(r"(^|\.)(localhost|127\.0\.0\.1)(:\d+)?$")


def site():
    """요청 호스트로 사이트를 고른다(서브도메인 = sites 1행). 기준 주소: 첫 사이트는 BASE_URL, 그 외는 https://{host}.
    로컬 미리보기(law.localhost:5000)에서는 요청 호스트를 그대로 써서 링크가 로컬에 머문다."""
    if "site" not in g:
        g.site = dbq.site(request.host)
        if g.site["host"] != current_app.config["BASE_URL"].split("://", 1)[-1]:
            g.base_url = f"{request.scheme}://{request.host}" if LOCAL_HOST.search(request.host) else f"https://{g.site['host']}"
    return g.site


def is_bot():
    ua = request.headers.get("User-Agent", "")
    return not ua or bool(BOT_RE.search(ua))


@web_bp.app_context_processor
def inject():
    s = site()
    return {"site": s, "settings": dbq.settings(s["id"]), "base_url": site_base(),
            "nav_verticals": dbq.verticals(s["id"]), "static_pages": STATIC_PAGES}


@web_bp.before_app_request
def normalize_url():
    """소문자·중복 하이픈/슬래시 정리·트레일링 슬래시 통일 후 301."""
    path = request.path
    if request.method not in ("GET", "HEAD") or path.startswith(("/go/", "/admin", "/static/", "/lead/", "/postback/")):
        return None
    new = re.sub(r"/{2,}", "/", re.sub(r"-{2,}", "-", path.lower()))
    if "." not in new.rsplit("/", 1)[-1] and not new.endswith("/"):
        new += "/"
    if new != path:
        qs = request.query_string.decode()
        return redirect(quote(new) + ("?" + qs if qs else ""), 301)
    return None


@web_bp.app_errorhandler(404)
def not_found(_e):
    return render_template("errors/404.html", title="페이지를 찾을 수 없습니다", robots="noindex,follow"), 404


@web_bp.app_errorhandler(500)
def server_error(_e):
    return "<h1>일시적인 오류가 발생했습니다</h1>", 500


@web_bp.get("/")
def home():
    s = site()
    st = dbq.settings(s["id"])
    return render_template(
        "pages/home.html", title=st.get("home_title") or f"{s['name']}{s['title_sep']}개인회생·파산 제휴 상담처 안내",
        description=st.get("home_desc") or "개인회생·개인파산 제도의 일반 정보와 광고 제휴 상담처를 안내하는 사이트입니다. 법률 자문을 제공하지 않으며 상담은 외부 상담처에서 진행됩니다.",
        canonical=abs_url("/"), hubs=dbq.published(s["id"], ("vertical_hub",)), topics=dbq.published(s["id"], ("topic_hub",)),
        regions=dbq.published_sido_rep(dbq.published_sido(s["id"])))


def _page(template, title, url, **ctx):
    s = site()
    return render_template(template, title=f"{title}{s['title_sep']}{s['name']}", h1=title, canonical=abs_url(url),
                           **{"description": f"{s['name']} {title} 페이지입니다.", **ctx})


def _tool(vertical, slug, url):
    tpl, title = TOOLS[slug]
    a, ctx = request.args, {"result": None}
    if tpl == "repayment":
        living = {int(re.sub(r"\D", "", p["item"])): p for p in ops.price_ranges(vertical["id"]) if p["item"].startswith("생계비")}
        ctx["living"] = living
        income, household = a.get("income", type=int), a.get("household", type=int)
        if income and household in living:
            available = max(income - living[household]["min_amount"], 0)
            ctx["result"] = {"income": income, "household": household, "living": living[household]["min_amount"],
                             "available": available, "total36": available * 36, "total60": available * 60}
    else:
        if a.get("done"):
            ctx["result"] = {k: bool(a.get(k)) for k in "abcde"}
    return _page(f"tools/{tpl}.html", title, url, robots="noindex,follow" if request.args else None,
                 description=f"{title}: 입력한 값으로 대략적인 방향을 확인하는 참고용 도구입니다. 실제 결과는 법원 판단에 따라 달라집니다.", **ctx)


def _special(s, url):
    """콤보가 아닌 공개 페이지: 정적, 작성자, HTML 사이트맵, 도구."""
    if url in STATIC_PAGES:
        tpl, title = STATIC_PAGES[url]
        return _page(f"static/{tpl}.html", title, url)
    m = re.fullmatch(r"/작성자/([^/]+)/", url)
    if m:
        author = ops.author(m.group(1)) or abort(404)
        return _page("pages/author.html", author["name"], url, author=author)
    if url == "/사이트맵/":
        return _page("pages/html_sitemap.html", "사이트맵", url, verticals=dbq.verticals(s["id"]),
                     hubs=dbq.published(s["id"], ("vertical_hub", "topic_hub")), tool_base=TOOL_VERTICAL,
                     tools=TOOLS if ops.vertical_by_base(s["id"], TOOL_VERTICAL) else {},
                     topics=dbq.published(s["id"], SITEMAP_TYPES["topic"], 300))
    m = re.fullmatch(r"(/[^/]+)/사이트맵/지역/", url)
    vertical = m and ops.vertical_by_base(s["id"], m.group(1))
    if vertical:
        return _page("pages/html_sitemap.html", f"{vertical['name']} 지역별 안내", url, regions=ops.sitemap_regions(vertical["id"]))
    m = re.fullmatch(r"(/[^/]+)/도구/([^/]+)/", url)
    if m and m.group(1) == TOOL_VERTICAL and m.group(2) in TOOLS:
        vertical = ops.vertical_by_base(s["id"], m.group(1)) or abort(404)
        return _tool(vertical, m.group(2), url)
    return None


def _ab_arms(combo):
    """실행 중인 A/B 테스트 → ({slot: (variant, offer_id)}, 방문자 쿠키). arms = [{"variant":"a","offer_id":1}, ...]"""
    tests = ops.running_ab_tests(combo["vertical_id"], combo["id"])
    if not tests:
        return None, None
    visitor = request.cookies.get("v") or secrets.token_hex(8)
    ab = {}
    for t in tests:
        arms = js(t["arms"], [])
        if arms:
            arm = arms[int(hashlib.md5(f"{visitor}|{t['id']}".encode()).hexdigest(), 16) % len(arms)]
            ab[t["slot"]] = (str(arm["variant"])[:20], arm.get("offer_id"))
    return ab, visitor


@web_bp.get("/<path:path>/")
def resolve_combo(path):
    s, url = site(), f"/{path}/"
    special = _special(s, url)
    if special:
        return special
    combo = dbq.combo_by_url(s["id"], url)
    if combo and combo["status"] in ("published", "noindex"):
        ab, visitor = _ab_arms(combo)
        html = render_combo(s, combo, ab)
        if html:
            if not is_bot():  # 슬롯 단위 노출 집계(봇 제외). nginx proxy_cache 를 켜면 캐시 적중분은 집계되지 않는다
                shown = [(slot, offer_id, "") for slot, offer_id in shown_offers(s, combo)]
                for slot, (variant, offer_id) in (ab or {}).items():  # A/B 슬롯은 arm 의 오퍼·variant 로 집계
                    shown = [x for x in shown if x[0] != slot or slot == "cards"] + [(slot, offer_id, variant)]
                ops.add_impressions(s["id"], combo["id"], list(dict.fromkeys(shown)))
            resp = Response(html, mimetype="text/html")
            if ab:
                resp.set_cookie("v", visitor, max_age=90 * 86400, httponly=True, samesite="Lax")
                resp.headers["Cache-Control"] = "private, no-store"
            return resp
    elif combo and combo["status"] in ("merged", "retired"):
        parent, hops = combo, 0
        while parent and parent["status"] != "published" and parent["parent_combo_id"] and hops < 8:  # 리다이렉트 체인 없이 공개된 조상으로 바로
            parent, hops = dbq.combo(parent["parent_combo_id"]), hops + 1
        if parent and parent["id"] != combo["id"] and parent["status"] == "published":
            return redirect(quote(parent["url_path"]), 301)
    r = dbq.redirect_for(s["id"], url)
    if r:
        return redirect(quote(r["to_path"]), r["code"] or 301)
    region_slug, _, service = path.rpartition("-")  # 레거시 평탄 URL → 디렉터리형 301
    if "/" not in path and service in LEGACY_SERVICES:
        target = dbq.combo_by_url(s["id"], f"{LEGACY_SERVICES[service]}/지역/{region_slug}/")
        if target and target["status"] == "published":
            return redirect(quote(target["url_path"]), 301)
    abort(404)


# ── /go/ 클릭 리다이렉터(스펙 7.3) ────────────────────────────────────────
def _base62(n):
    out = ""
    while n:
        n, r = divmod(n, 62)
        out = B62[r] + out
    return out.rjust(22, "0")


def _hash(value):
    # IP 해시는 일 단위 salt
    return hashlib.md5(f"{current_app.config['SECRET_KEY']}|{date.today()}|{value}".encode()).hexdigest()


def log_click(offer, combo_id=None, slot=None, variant=None, keyword_id=None):
    combo = dbq.combo(combo_id) if combo_id else None
    region = dbq.region(combo["region_id"]) if combo and combo["region_id"] else None
    ua = request.headers.get("User-Agent", "")
    click = {
        "click_id": _base62(uuid.uuid4().int), "offer_id": offer["id"], "combo_id": combo and combo["id"],
        "region_id": region and region["id"], "keyword_term_id": keyword_id or (combo and combo["topic_id"]),
        "slot": (slot or "")[:30] or None, "variant": (variant or "")[:20] or None,
        "ua_hash": hashlib.md5(ua.encode()).hexdigest(), "ip_hash": _hash(request.remote_addr),
        "referrer": (request.referrer or "")[:255] or None,
        "is_bot": int(is_bot()),  # ponytail: UA 규칙만. 빈도 기반 판정은 트래픽이 생기면 추가
    }
    dbq.insert_click(**click)
    return click, combo, region


@go_bp.get("/go/<offer_slug>")
def redirect_click(offer_slug):
    offer = dbq.eligible_offer_by_slug(offer_slug)
    if not offer:  # 종료·중지된 오퍼는 버티컬 fallback 으로
        known = dbq.offer_by_slug(offer_slug)
        vertical = known and dbq.vertical(known["vertical_id"])
        offer = vertical and vertical["fallback_offer_id"] and dbq.eligible_offer(vertical["fallback_offer_id"])
        if not offer:
            abort(404)
    s, a = site(), request.args
    click, combo, region = log_click(offer, a.get("p", type=int), a.get("s"), a.get("ab"), a.get("k", type=int))
    mode = offer["integration_mode"]
    if mode == "phone":
        url = "tel:" + re.sub(r"[^\d+]", "", offer["phone"] or "")
    elif mode == "own_form":
        url = f"/lead/{offer['slug']}?c={click['click_id']}"
    else:
        template = {"iframe": offer["embed_url_template"], "kakao": offer["kakao_url"]}.get(mode)
        url = get_adapter(dbq.network(offer["network_id"])).build_tracking_url(offer, click, {
            "page": combo["url_path"] if combo else "", "region_slug": region["path_slug"] if region else "",
            "region_full": region["path_name"] if region else "", "keyword": combo["primary_keyword"] if combo else "",
            "combo_id": combo["id"] if combo else "", "slot": click["slot"], "variant": click["variant"], "site": s["host"],
            "utm_source": s["host"], "utm_medium": "cpa", "utm_campaign": offer["slug"]}, template)
    if not url or url == "tel:":
        abort(404)
    resp = redirect(url, 302)  # 봇도 로그만 남기고 동일하게 302
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ── 포스트백(스펙 7.4): verify → parse → upsert ───────────────────────────
@postback_bp.route("/postback/<network_slug>", methods=["GET", "POST"])
def postback(network_slug):
    network = ops.network_by_slug(network_slug) or abort(404)
    adapter = get_adapter(network)
    if not adapter.verify_postback(request):
        dbq.audit("postback", "postback_rejected", f"networks:{network['id']}",
                  json.dumps({"ip": request.remote_addr, "params": sorted(request.values.keys())}))  # 값(시크릿)은 남기지 않는다
        abort(403)
    resp = Response(conv.record_conversion(network, adapter.parse_postback(request), "postback"), mimetype="text/plain")
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ── 자체 폼(Phase 8). OWN_FORM_ENABLED=false 면 전부 404 ──────────────────
def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(current_app.config["LEAD_KEY"].encode())


@lead_bp.route("/lead/<offer_slug>", methods=["GET", "POST"])
def lead(offer_slug):
    cfg, s = current_app.config, site()
    offer = dbq.eligible_offer_by_slug(offer_slug)
    st = dbq.settings(s["id"])
    consent = {"collect": st.get("consent_collect_text"), "third": st.get("consent_third_party_text"), "version": st.get("consent_version")}
    # 플래그·암호화 키·동의문(2종+버전) 중 하나라도 없으면 폼을 내보내지 않는다
    if not (cfg["OWN_FORM_ENABLED"] and cfg["LEAD_KEY"] and offer and offer["integration_mode"] == "own_form" and all(consent.values())):
        abort(404)
    consent["third"] = consent["third"].replace("{advertiser}", offer["advertiser"])
    errors, form = [], {}
    if request.method == "POST":
        form = request.form
        if not hmac.compare_digest(form.get("csrf", ""), session.get("lead_csrf", "-")):
            abort(400)
        ip_hash = _hash(request.remote_addr)
        if ops.recent_leads_from(ip_hash) >= 5:
            abort(429)
        name, phone = form.get("name", "").strip()[:40], re.sub(r"\D", "", form.get("phone", ""))
        if not name:
            errors.append("이름을 입력해 주세요.")
        if not PHONE_RE.match(phone):
            errors.append("연락처를 숫자로 정확히 입력해 주세요. 예: 01012345678")
        if not (form.get("consent_collect") and form.get("consent_third_party")):
            errors.append("개인정보 수집·이용 동의와 제3자 제공 동의가 모두 필요합니다.")
        if not errors:
            click = ops.click(form.get("c", ""))
            f = _fernet()
            days = int(st.get("lead_retention_days", 90))
            lead_id = ops.insert_lead(
                offer_id=offer["id"], click_id=click and click["click_id"], combo_id=click and click["combo_id"],
                name_enc=f.encrypt(name.encode()), phone_enc=f.encrypt(phone.encode()), region_id=click and click["region_id"],
                fields=json.dumps({"time": form.get("time", "")[:40]}, ensure_ascii=False), consent_collect=1, consent_third_party=1,
                consent_version=consent["version"], consent_text_hash=hashlib.md5((consent["collect"] + consent["third"]).encode()).hexdigest(),
                ip_hash=ip_hash, retention_until=date.today() + timedelta(days=days))
            try:
                result = get_adapter(dbq.network(offer["network_id"])).forward_lead(
                    offer, {"lead_id": lead_id, "name": name, "phone": phone, "time": form.get("time", "")[:40], "offer": offer["slug"]})
            except Exception as e:  # 전달 실패해도 리드는 저장돼 있다. 관리자에서 상태 확인
                result = {"ok": False, "status": f"{type(e).__name__}: {e}"}
            ops.lead_forwarded(lead_id, result, None)
            session.pop("lead_csrf", None)
            return _lead_page("forms/lead_done.html", offer)
    session["lead_csrf"] = session.get("lead_csrf") or secrets.token_hex(16)
    return _lead_page("forms/lead_form.html", offer, consent=consent, errors=errors, form=form, click_id=request.values.get("c", ""))


def _lead_page(template, offer, **ctx):
    resp = Response(_page(template, f"{offer['advertiser']} 상담 신청", f"/lead/{offer['slug']}", robots="noindex,nofollow", offer=offer, **ctx))
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ── SEO ───────────────────────────────────────────────────────────────────
def _xml(template, **ctx):
    return Response(render_template(template, **ctx), mimetype="application/xml")


@seo_bp.get("/robots.txt")
def robots():
    sm = "/sitemap-index.xml" if site()["id"] == 1 else "/sitemap.xml"
    lines = ["User-agent: *", "Allow: /", "Disallow: /go/", "Disallow: /lead/", "Disallow: /postback/", "Disallow: /admin/",
             "", f"Sitemap: {abs_url(sm)}", ""]
    return Response("\n".join(lines), mimetype="text/plain")


@seo_bp.get("/<name>.txt")
def indexnow_key(name):
    key = site()["indexnow_key"]
    if not key or name != key:
        abort(404)
    return Response(key, mimetype="text/plain")


@seo_bp.get("/sitemap.xml")
@seo_bp.get("/sitemap-index.xml")
def sitemap_index():
    s, files = site(), ["sitemap-static-1.xml"]
    for stype, page_types in SITEMAP_TYPES.items():
        files += [f"sitemap-{stype}-{i}.xml" for i in range(1, ceil(dbq.count_published(s["id"], page_types) / SITEMAP_CHUNK) + 1)]
    return _xml("xml/sitemap_index.xml", files=[abs_url("/" + f) for f in files])


@seo_bp.get("/sitemap-<stype>-<int:n>.xml")
def sitemap(stype, n):
    if stype == "static" and n == 1:
        s = site()
        tools = TOOLS if ops.vertical_by_base(s["id"], TOOL_VERTICAL) else {}
        paths = ["/", *STATIC_PAGES, "/사이트맵/", *[f"{TOOL_VERTICAL}/도구/{t}/" for t in tools],
                 *[f"{v['url_base']}/사이트맵/지역/" for v in dbq.verticals(s["id"])]]
        return _xml("xml/urlset.xml", urls=[{"loc": abs_url(p), "lastmod": None} for p in paths])
    if stype not in SITEMAP_TYPES or n < 1:
        abort(404)
    rows = dbq.published(site()["id"], SITEMAP_TYPES[stype], SITEMAP_CHUNK, (n - 1) * SITEMAP_CHUNK)
    if not rows:
        abort(404)
    return _xml("xml/urlset.xml", urls=[{"loc": abs_url(r["url_path"]), "lastmod": r["content_updated_at"]} for r in rows])


@seo_bp.get("/rss.xml")
def rss():
    items = [{**r, "link": abs_url(r["url_path"])} for r in dbq.latest_published(site()["id"])]
    return _xml("xml/rss.xml", items=items, feed_title=site()["name"])


@seo_bp.get("/<vertical>/rss.xml")
def vertical_rss(vertical):
    v = ops.vertical_by_base(site()["id"], "/" + vertical) or abort(404)
    items = [{**r, "link": abs_url(r["url_path"])} for r in ops.latest_published_vertical(v["id"])]
    return _xml("xml/rss.xml", items=items, feed_title=f"{site()['name']} {v['name']}")
