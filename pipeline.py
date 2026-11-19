"""콘텐츠 파이프라인(스펙 8.7): 생성(Claude) → 린트·고유성·팩트 플래그 → 검수 → 게이트 → 공개. 요청 처리 중에는 호출하지 않는다."""
import json
import re
from pathlib import Path

from jinja2 import Template

from db import js, ops, queries as dbq
from engine import FORBIDDEN, UNIQUENESS_MAX, _court, gate, lint

PROMPTS = Path(__file__).parent / "prompts"
PROMPT_VERSION = "p1"
MODEL_HUB, MODEL_LEAF = "claude-sonnet-5-5", "claude-haiku-4-5"  # 스펙 8.7 모델 분담
MODEL_POST = "claude-opus-5-5"  # 질문 글(법률·보험 일반 정보라 표현 실수가 적은 모델). Batch API 로 절반 가격
PRICE = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.1, 0.5)}  # $/1M 입력·출력(일괄은 절반)
HUB_TYPES = ("vertical_hub", "topic_hub")
MAX_ATTEMPTS = 3
AUTO_APPROVE_PERCENT = 50  # 지역 리프: 린트·고유성 통과분 중 자동 승인 비율(초기 3개월 50%, 이후 80%로)
SYSTEM = ("당신은 한국어 정보성 웹 페이지의 편집자입니다. 주어진 사실 자료 안에서만 쓰고, 자료에 없는 법령·기간·금액 수치는 쓰지 않습니다. "
          "결과를 약속하거나 단정적으로 법률 조언을 하지 않습니다. 응답은 요청한 JSON 객체 하나만 출력합니다.")
TAG_RE = re.compile(r"<[^>]+>")
NUM_RE = re.compile(r"\d[\d,.]*\s*(?:억|만|천)?\s*(?:원|년|개월|일|%|조|회)")


def plain(blocks):
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", " ".join(b["html"] for b in blocks))).strip()


def shingles(text, n=5):
    words = text.split()
    return {" ".join(words[i:i + n]) for i in range(max(len(words) - n + 1, 1))}


def uniqueness(combo, blocks):
    """형제 페이지와의 5-gram(어절) Jaccard 최댓값. 형제가 없으면 0."""
    mine, worst = shingles(plain(blocks)), 0.0
    for s in ops.sibling_contents(combo):
        other = shingles(plain(js(s["blocks"], [])))
        worst = max(worst, len(mine & other) / (len(mine | other) or 1))
    return round(worst, 3)


def fact_flags(text, facts):
    """topic_facts 에 없는 수치 표현(검수자가 확인할 목록)."""
    known = re.sub(r"\s+", "", " ".join(f["fact_text"] for f in facts))
    return sorted({m for m in NUM_RE.findall(text) if re.sub(r"\s+", "", m) not in known})


def score(combo, content):
    """린트·고유성·팩트 플래그를 계산해 저장하고 (uniqueness, lint_result) 를 돌려준다."""
    vertical = dbq.vertical(combo["vertical_id"])
    blocks, faqs = js(content["blocks"], []), js(content["faqs"], [])
    text = " ".join([content["h1"] or "", content["title"] or "", content["meta_description"] or "", plain(blocks)]
                    + [f["question"] + " " + f["answer"] for f in faqs])
    result, uniq = lint(vertical, text), uniqueness(combo, blocks)
    flags = fact_flags(text, dbq.topic_facts(vertical["id"], combo["topic_id"]))
    status = None
    if content["status"] in ("generated", "lint_fail", "lint_ok"):
        status = "lint_ok" if result["ok"] else "lint_fail"
    ops.save_scores(content["id"], uniq, result, flags, status)
    return uniq, result


def score_all(site_id):
    """모든 콤보의 현재 버전을 다시 채점."""
    n = 0
    for combo in ops.combos_by_status(site_id, ["approved", "published", "review", "generated", "noindex"]):
        content = combo["content_version"] and dbq.content(combo["id"], combo["content_version"])
        if content:
            score(combo, content)
            n += 1
    return n


def gate_audit(site):
    """공개 중인 콤보를 게이트로 재검사해 실패분을 review 로 내린다. {url: 사유}"""
    demoted = {}
    for combo in ops.combos_by_status(site["id"], ["published"]):
        fails = gate(site, combo)
        if fails:
            ops.set_combo(combo["id"], status="review")
            demoted[combo["url_path"]] = fails
    if demoted:
        dbq.bump_cache_version(site["id"])
    return demoted


def publish_ready(site, cap=None):
    """approved 콤보를 우선순위 순으로 게이트 검사 후 공개. 주간 상한(cap) 안에서만. [(url, 실패사유 or None)]

    내부 링크 수는 공개된 페이지끼리 세므로, 이번 묶음을 먼저 공개 상태로 두고 검사한 뒤 실패분을 approved 로 되돌린다.
    되돌린 페이지 때문에 다른 페이지의 링크가 줄 수 있어 더 떨어지는 것이 없을 때까지 반복한다.
    """
    from datetime import date
    from engine import abs_url
    st = dbq.settings(site["id"])
    plan = js(st.get("publish_plan"), [])
    scheduled = cap is None  # cap 을 직접 넘긴 호출(테스트·수동)은 질문 글 몫을 열지 않는다
    if cap is None and plan:  # 날짜별 하루 공개 수(오픈 첫 며칠은 많이, 이후 하루 몇백 개)
        today = date.today().isoformat()
        step = next((p for p in plan if not p.get("until") or today <= p["until"]), plan[-1])
        room = max(int(step["per_day"]) - ops.published_today(site["id"]), 0)
    else:
        if cap is None:
            cap = int(st.get("weekly_publish_cap", 100))
        room = max(cap - ops.published_last_days(site["id"]), 0)
    batch = ops.combos_by_status(site["id"], ["approved"], limit=room, posts=False) if room else []
    posts_room = max(int(st.get("posts_per_day") or 0) - ops.published_today(site["id"], posts=True), 0) if scheduled else 0
    if posts_room:  # 질문 글: 미리 써서 승인해 둔 글을 하루 posts_per_day 편씩(우선순위 → id 순)
        batch += ops.combos_by_status(site["id"], ["approved"], limit=posts_room, posts=True)
    for combo in batch:
        ops.publish_combo(combo["id"])
    failed, changed = {}, True
    while changed:
        changed = False
        for combo in batch:
            if combo["id"] not in failed:
                fails = gate(site, dbq.combo(combo["id"]))
                if fails:
                    failed[combo["id"]] = fails
                    ops.set_combo(combo["id"], status="approved", published_at=combo["published_at"])
                    changed = True
    for combo in batch:
        if combo["id"] not in failed:
            dbq.queue_indexnow(site["id"], abs_url(combo["url_path"]), "publish")
    if batch:
        dbq.bump_cache_version(site["id"])
    return [(c["url_path"], failed.get(c["id"])) for c in batch]


# ── 생성 ──────────────────────────────────────────────────────────────────
def build_prompt(combo, feedback=""):
    vertical = dbq.vertical(combo["vertical_id"])
    region = dbq.region(combo["region_id"]) if combo["region_id"] else None
    path = PROMPTS / f"{combo['page_type']}.md"
    template = Template((path if path.exists() else PROMPTS / "default.md").read_text("utf-8"))
    return template.render(
        combo=combo, vertical=vertical, region=region, court=_court(region),
        topic=dbq.term(combo["topic_id"]) if combo["topic_id"] else None,
        intent=dbq.term(combo["intent_id"]) if combo["intent_id"] else None,
        situation=dbq.term(combo["situation_id"]) if combo["situation_id"] else None,
        persona=dbq.term(combo["persona_id"]) if combo["persona_id"] else None,
        region_children=dbq.region_children(region["id"]) if region else [],
        region_facts=dbq.region_facts(region["id"]) if region else [],
        region_programs=dbq.region_programs(region["id"], vertical["id"]) if region else [],
        facts=dbq.topic_facts(vertical["id"], combo["topic_id"]),
        forbidden=FORBIDDEN + js(vertical["compliance_schema"], {}).get("badges_forbidden", []), feedback=feedback)


def call_claude(model, system, prompt):
    import anthropic  # 배치에서만 쓰므로 지연 import
    response = anthropic.Anthropic().messages.create(
        model=model, max_tokens=16000, system=system, messages=[{"role": "user", "content": prompt}])
    if response.stop_reason in ("refusal", "max_tokens"):
        raise ValueError(f"생성 중단: {response.stop_reason}")
    return "".join(b.text for b in response.content if b.type == "text")


def parse_output(text):
    data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    for key in ("h1_candidates", "meta_description", "blocks"):
        if not data.get(key):
            raise ValueError(f"출력에 {key} 없음")
    for b in data["blocks"]:
        if b.get("slot") not in ("summary", "intro", "body") or not b.get("html"):
            raise ValueError("blocks 형식 오류")
    data["faqs"] = [{"question": f["q"], "answer": f["a"]} for f in data.get("faqs", [])]
    data["h1"] = data["h1_candidates"][0]
    return data


def _accept(site, combo, data, model):
    """생성 결과 1건을 새 버전으로 저장하고 채점. (content_id, uniqueness, lint_result)"""
    last = ops.latest_content(combo["id"])
    version = (last["version"] if last else 0) + 1
    data["title"] = data["h1"] + site["title_sep"] + site["name"]
    content_id = ops.insert_content(combo["id"], version, data, model, PROMPT_VERSION)
    ops.set_combo(combo["id"], content_version=combo["content_version"] or version)  # 형제 비교용 포인터(공개본이 있으면 유지)
    uniq, result = score(combo, ops.content_by_id(content_id))
    return content_id, uniq, result


def _finish(site, combo, content_id, passed):
    """채점 결과에 따라 검수 큐 또는(지역 리프 샘플만) 자동 승인. 최종 상태를 돌려준다."""
    if passed and combo["page_type"] == "question":  # 질문 글: 린트·고유성 통과분은 전부 승인(사용자 결정 2026-11-19 — 미리 써서 자동 발행)
        approve(site, content_id, "auto-post")
        return "approved"
    if passed and combo["page_type"] not in HUB_TYPES and combo["id"] * 37 % 100 < AUTO_APPROVE_PERCENT:  # 허브는 100% 사람 검수
        approve(site, content_id, "auto")
        return "approved"
    if not passed:
        result = js(ops.content_by_id(content_id)["lint_result"], {})
        ops.review_content(content_id, "lint_fail" if not result.get("ok") else "review", None)
    else:
        ops.review_content(content_id, "review", None)
    if combo["status"] in ("draft", "generated"):
        ops.set_combo(combo["id"], status="review")
    return "review"


def generate(site, combo, call=call_claude):
    """콤보 1개 생성(API). 린트·고유성 실패 시 피드백을 넣어 최대 3회 재생성. 최종 page_contents.status 를 돌려준다."""
    model = MODEL_HUB if combo["page_type"] in HUB_TYPES else MODEL_LEAF
    feedback = ""
    for _attempt in range(MAX_ATTEMPTS):
        content_id, uniq, result = _accept(site, combo, parse_output(call(model, SYSTEM, build_prompt(combo, feedback))), model)
        if result["ok"] and uniq <= UNIQUENESS_MAX:
            return _finish(site, combo, content_id, True)
        feedback = "이전 시도의 문제: " + "; ".join(
            ([f"금지 표현 사용({', '.join(result['forbidden'])})"] if result["forbidden"] else [])
            + (["'~하면 됩니다' 식 단정 문장 사용"] if result["advice"] else [])
            + ([f"형제 페이지와 문장이 너무 비슷함(유사도 {uniq})"] if uniq > UNIQUENESS_MAX else []))
    return _finish(site, combo, content_id, False)  # 3회 실패 → 검수 큐


def submit(site, combo, text, model="claude-code"):
    """API 대신 Claude Code 세션 등에서 쓴 결과(JSON 문자열)를 한 번에 넣는다. 같은 채점·검수 경로를 탄다. (status, uniqueness, lint)"""
    content_id, uniq, result = _accept(site, combo, parse_output(text), model)
    return _finish(site, combo, content_id, result["ok"] and uniq <= UNIQUENESS_MAX), uniq, result


def approve(site, content_id, reviewer):
    """검수 승인: 이 버전을 공개 버전으로 지정. 실제 공개는 publish_ready(게이트·주간 상한)가 한다."""
    content = ops.content_by_id(content_id)
    combo = dbq.combo(content["combo_id"])
    ops.review_content(content_id, "approved", reviewer)
    ops.set_combo(combo["id"], content_version=content["version"],
                  status=combo["status"] if combo["status"] in ("published", "noindex") else "approved")
    score(dbq.combo(combo["id"]), ops.content_by_id(content_id))
    dbq.bump_cache_version(site["id"])


def reject(content_id, reviewer):
    ops.review_content(content_id, "rejected", reviewer)
