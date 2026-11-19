"""오퍼 선택(스펙 7.6), 콘텐츠 조립(10.3), 품질 게이트."""
import hashlib
import json
import random
import re
from pathlib import Path
from urllib.parse import quote

from flask import current_app, g, render_template

from og import card_path
from db import js, ops, queries as dbq

STATIC_PATHS = ["/운영사/", "/광고고지/", "/편집기준/", "/개인정보처리방침/", "/이용약관/", "/면책조항/"]
CARD_COUNT_REGION = 3  # 지역(롱테일) 페이지는 제휴처 카드를 줄여 사이트 안 같은 문장을 낮춘다(순서는 페이지 시드로 달라짐)
CARD_COUNT = 8                 # 스펙 10.3의 vertical.card_count 는 10.1 DDL에 컬럼이 없어 상수로 둔다
W_EPC, W_PAYOUT, W_MANUAL, W_REJECT = 2.0, 1.0, 1.0, 1.0
COLD_START_CLICKS = 50         # 7일 클릭이 이보다 적으면 payout·manual_weight 만 사용
FAQ_COUNT = 6
UNIQUENESS_MAX = 0.35
FORBIDDEN = [w for w in (Path(__file__).parent / "lint" / "forbidden.txt").read_text("utf-8").split("\n") if w.strip()]
ADVICE_RE = re.compile(r"하시?면 됩니다")  # 법률 자문형 단정 문장


def lint_text(text, extra=()):
    """금지 표현 목록에 걸린 단어들. 빈 리스트면 통과."""
    return [w for w in dict.fromkeys([*FORBIDDEN, *extra]) if w in (text or "")]


_PLACES = None


def _strip_places(text):
    """행정구역 이름을 지운 텍스트(지명이 금지어를 품고 있어도 걸리지 않게)."""
    global _PLACES
    if _PLACES is None:  # 지명 약 3천 개를 한 번에 찾는 정규식(긴 이름 먼저) — 문장마다 3천 번 replace 하던 것을 대신한다
        names = sorted({r["short_name"] for r in dbq.region_names() if len(r["short_name"]) > 1}, key=len, reverse=True)
        _PLACES = re.compile("|".join(map(re.escape, names))) if names else None
    return _PLACES.sub(" ", text) if _PLACES else text


def lint(vertical, text):
    """컴플라이언스 린트 결과(page_contents.lint_result 에 저장되는 형태)."""
    text = _strip_places(text or "")
    forbidden = lint_text(text, js(vertical["compliance_schema"], {}).get("badges_forbidden", []))
    advice = ADVICE_RE.findall(text or "")
    return {"ok": not forbidden and not advice, "forbidden": forbidden, "advice": advice}


def compliance_ok(vertical, fields):
    required = js(vertical["compliance_schema"], {}).get("required", [])
    return all((fields or {}).get(k) for k in required)


def site_base():
    """현재 요청의 사이트 기준 주소. 서브도메인 사이트는 web.site() 가 g.base_url 을 채운다."""
    return g.get("base_url") or current_app.config["BASE_URL"]


def abs_url(path):
    return site_base() + quote(path)


# ── 오퍼 선택 ─────────────────────────────────────────────────────────────
def _in_scope(scope_json, value):
    scope = js(scope_json, [])
    return not scope or value in scope


def select_offers(site, combo, slot, n, vertical=None, region=None, topic=None):
    vertical = vertical or dbq.vertical(combo["vertical_id"])
    if region is None and combo["region_id"]:
        region = dbq.region(combo["region_id"])
    if topic is None and combo["topic_id"]:
        topic = dbq.term(combo["topic_id"])
    snap = js(dbq.settings(site["id"]).get("offer_snapshot"), {})  # rerank 배치가 만든 EPC·캡 스냅샷(결정성 유지)
    stats, capped = snap.get("offers", {}), set(snap.get("capped", []))
    sido = region["path_slug"].split("-")[0] if region else None
    own_form = current_app.config["OWN_FORM_ENABLED"]
    cand = [o for o in dbq.eligible_offers(combo["vertical_id"])
            if _in_scope(o["region_scope"], sido) and _in_scope(o["topic_scope"], topic["slug"] if topic else None)
            and o["id"] not in capped and (own_form or o["integration_mode"] != "own_form")]
    # 수동 플레이스먼트: block 제거, weight 덮어쓰기, pin 최우선. (DDL상 scope_id 가 정수라 page_type 범위는 지원하지 않는다)
    pins = {}
    for p in ops.placements(slot, vertical["id"], combo["id"]):
        if p["mode"] == "block":
            cand = [o for o in cand if o["id"] != p["offer_id"]]
        elif p["mode"] == "weight":
            cand = [{**o, "manual_weight": p["weight"]} if o["id"] == p["offer_id"] else o for o in cand]
        else:
            pins[p["offer_id"]] = p["position"] or 0
    if not cand:
        fb = vertical["fallback_offer_id"] and dbq.eligible_offer(vertical["fallback_offer_id"])
        return [fb] if fb else []
    if slot == "form" and not pins:  # 폼/상단 CTA 슬롯은 버티컬 기본 오퍼 우선
        default = [o for o in cand if o["id"] == vertical["form_offer_id"]]
        if default:
            return default[:n]
    max_pay = max(float(o["payout_amount"] or 0) for o in cand) or 1.0
    max_w = max(o["manual_weight"] or 0 for o in cand) or 1
    max_epc = max((stats.get(str(o["id"]), {}).get("epc", 0) for o in cand), default=0) or 1.0
    rng = random.Random(hashlib.sha256(f"{site['id']}|{combo['id']}|{slot}|{site['ranking_version']}".encode()).hexdigest())

    def key(o):  # 가중 셔플(Efraimidis-Spirakis). cand 가 id 순이라 난수 소비 순서도 결정적
        st = stats.get(str(o["id"]), {})
        score = W_PAYOUT * float(o["payout_amount"] or 0) / max_pay + W_MANUAL * (o["manual_weight"] or 0) / max_w
        if st.get("clicks", 0) >= COLD_START_CLICKS:
            score += W_EPC * st.get("epc", 0) / max_epc - W_REJECT * st.get("reject_rate", 0)
        return rng.random() ** (1.0 / max(score, 0.01))

    ranked = sorted(cand, key=key, reverse=True)
    pinned = sorted((o for o in ranked if o["id"] in pins), key=lambda o: pins[o["id"]])
    return (pinned + [o for o in ranked if o["id"] not in pins])[:n]


# ── 조립 ──────────────────────────────────────────────────────────────────
def _court(region):
    """법원 정보는 가장 가까운 상위 지역 것을 물려받는다."""
    while region and not region["court_name"]:
        region = dbq.region(region["parent_id"]) if region["parent_id"] else None
    return region


def _crumbs(combo):
    chain, cur = [], combo
    while cur:
        chain.append({"name": cur["primary_keyword"], "path": cur["url_path"]})
        cur = dbq.combo(cur["parent_combo_id"]) if cur["parent_combo_id"] else None
    chain.append({"name": "홈", "path": "/"})
    return [{**c, "url": abs_url(c["path"])} for c in reversed(chain) if c["path"]]


def _links(combo, region):
    links = {"parent": [], "children": dbq.children(combo["id"]),
             "siblings": dbq.siblings(combo) if combo["parent_combo_id"] else [],
             "other_topics": dbq.other_topics(combo) if region else [],
             "adjacent": ops.adjacent_combos(combo) if region else [],
             "regions": dbq.vertical_regions(combo["vertical_id"]) if combo["page_type"] == "question" else []}
    parent = dbq.combo(combo["parent_combo_id"]) if combo["parent_combo_id"] else None
    if parent and parent["url_path"]:
        links["parent"] = [parent]
    # 앵커 텍스트 변형: 같은 대상이라도 출발 페이지마다 다른 앵커를 결정적으로 고른다
    targets = [l for group in links.values() for l in group]
    variants = ops.anchor_variants([l["id"] for l in targets])
    for l in targets:
        opts = variants.get(l["id"])
        l["anchor"] = opts[int(hashlib.md5(f"{combo['id']}|{l['id']}".encode()).hexdigest(), 16) % len(opts)] if opts else l["primary_keyword"]
    return links


def assemble(site, combo, ab=None):
    """렌더에 필요한 모든 것. 승인된 콘텐츠가 없으면 None. ab = {slot: (variant, offer_id)}"""
    content = dbq.content(combo["id"], combo["content_version"])
    if not content:
        return None
    vertical = dbq.vertical(combo["vertical_id"])
    for k in ("trust_bullets", "cautions", "procedure_tabs", "personas"):
        vertical[k] = js(vertical[k], [])
    region = dbq.region(combo["region_id"]) if combo["region_id"] else None
    topic = dbq.term(combo["topic_id"]) if combo["topic_id"] else None
    page = dbq.page(combo["id"])
    ctx = {
        "combo": combo, "content": content, "blocks": js(content["blocks"], []), "vertical": vertical,
        "region": region, "court": _court(region), "topic": topic,
        "region_children": dbq.region_children(region["id"]) if region else [],
        "region_facts": dbq.region_facts(region["id"]) if region else [],
        "region_programs": dbq.region_programs(region["id"], vertical["id"]) if region else [],
        "topic_facts": dbq.topic_facts(vertical["id"], combo["topic_id"]),
        "price_ranges": ops.price_ranges(vertical["id"], region["id"]) if region else [],
        "offers_main": select_offers(site, combo, "cards", _card_count(combo), vertical, region, topic),
        "offer_form": select_offers(site, combo, "form", 1, vertical, region, topic),
        "links": _links(combo, region), "crumbs": _crumbs(combo),
        "author": ops.default_author(), "og_image": card_path(combo), "ab": {},
        "own_form_enabled": current_app.config["OWN_FORM_ENABLED"],
    }
    for slot, (variant, offer_id) in (ab or {}).items():  # A/B: 해당 슬롯 맨 앞을 arm 의 오퍼로 교체
        arm = dbq.eligible_offer(offer_id)
        key = "offer_form" if slot == "form" else "offers_main"
        if arm:
            ctx[key] = [arm] + [o for o in ctx[key] if o["id"] != arm["id"]][: (0 if slot == "form" else CARD_COUNT - 1)]
            ctx["ab"][slot] = variant
    faqs = js(content["faqs"])
    if not faqs:  # 생성 FAQ 우선, 없으면 풀에서 combo.id 시드로 결정적 추출
        pool = dbq.faq_candidates(vertical["id"], combo["topic_id"])
        faqs = random.Random(combo["id"]).sample(pool, min(FAQ_COUNT, len(pool)))
    ctx["faqs"] = faqs
    ctx["canonical"] = abs_url(combo["url_path"])
    ctx["robots"] = "noindex,follow" if combo["status"] == "noindex" or not ctx["offers_main"] else None
    test = ops.running_title_test(combo["id"])  # 제목·설명 순차 테스트
    ctx["title"] = (test and test["variant_title"]) or content["title"]
    ctx["description"] = (test and test["variant_desc"]) or content["meta_description"]
    return ctx


def render_combo(site, combo, ab=None):
    cached = dbq.page(combo["id"])
    ctx = assemble(site, combo, ab)
    if ctx is None:
        return None
    html = render_template(f"pages/{combo['page_type']}.html", **ctx)
    if ab:  # A/B 변형은 방문자별이라 캐시하지 않는다
        return html
    order = [o["id"] for o in ctx["offers_main"] + ctx["offer_form"]]
    content_hash = hashlib.md5(json.dumps([ctx["blocks"], ctx["faqs"], order], ensure_ascii=False).encode()).hexdigest()
    noindex = int(ctx["robots"] is not None)
    if cached and cached["content_hash"] == content_hash and cached["noindex"] == noindex:
        return html  # 바뀐 게 없으면 쓰기 생략(HTML 은 저장하지 않는다 — 대량 페이지에서 DB 가 HTML 로 불어나지 않게)
    dbq.upsert_page(combo["id"], None, site["cache_version"], site["ranking_version"], content_hash, 1 - noindex, noindex)
    if not cached or not cached["content_hash"]:
        dbq.touch_combo(combo["id"], only_if_null=True)
    elif cached["content_hash"] != content_hash:
        dbq.touch_combo(combo["id"])
        dbq.queue_indexnow(site["id"], ctx["canonical"], "update")
    return html


def _card_count(combo):
    """지역·질문 글(롱테일)은 카드 3장, 허브는 8장."""
    return CARD_COUNT_REGION if combo["region_id"] or combo["page_type"] == "question" else CARD_COUNT


def shown_offers(site, combo):
    """노출 집계용: 이 콤보에 지금 표시되는 (slot, offer_id) 목록."""
    return ([("cards", o["id"]) for o in select_offers(site, combo, "cards", _card_count(combo))]
            + [("form", o["id"]) for o in select_offers(site, combo, "form", 1)])


# ── 품질 게이트 ───────────────────────────────────────────────────────────
def gate(site, combo):
    """publish 전 검사. 실패 사유 리스트(빈 리스트 = 통과). 우회 경로를 만들지 말 것."""
    ctx = assemble(site, combo)
    if ctx is None:
        return ["승인된 콘텐츠 없음"]
    fails, c = [], ctx["content"]
    if c["uniqueness_score"] is None:
        fails.append("uniqueness 미계산")
    elif float(c["uniqueness_score"]) > UNIQUENESS_MAX:
        fails.append(f"uniqueness {float(c['uniqueness_score']):.2f} > {UNIQUENESS_MAX}")
    lint_result = js(c["lint_result"])
    if not lint_result or not lint_result.get("ok"):
        fails.append("lint 미통과" if lint_result else "lint 미실행")
    if ctx["region"]:  # 실데이터 2블록 규칙은 리프(지역) 대상(스펙 8.8). 허브는 사람 검수
        real = sum([bool(ctx["court"]), any(f["source_url"] for f in ctx["topic_facts"]),
                    bool(ctx["region_children"] or ctx["region_facts"] or ctx["region_programs"])])
        if real < 2:
            fails.append(f"실데이터 블록 {real}개")
    if not ctx["offers_main"]:
        fails.append("compliance_ok 오퍼 없음")
    urls = {x["path"] for x in ctx["crumbs"][:-1]} | {x["url_path"] for group in ctx["links"].values() for x in group}
    if len(urls) < 5:
        fails.append(f"내부 링크 {len(urls)}개")
    return fails
