"""키워드 공간(스펙 8): 정규화·차원 태깅, 콤보 생성(8.4 규칙), 카니벌 그룹, 검색량 반영, 네이버 검색광고 API."""
import base64
import hashlib
import hmac
import json
import math
import os
import re
import time
import urllib.request
from collections import defaultdict
from urllib.parse import quote

from db import ops, queries as dbq

VOLUME_MIN, VOLUME_MIN_REGION = 30, 10            # 8.4 (a) 독립 페이지 임계값(월 검색량 PC+모바일)
SIDO_INTENTS, SGG_INTENTS = ["비용", "추천", "잘하는곳", "무료상담"], ["비용", "추천"]   # 8.3
SGG_TOPIC_COUNT = 2                               # 시군구는 앞의 topic 2개만
COMMERCIAL = {"비용", "추천", "상담", "무료상담", "잘하는곳", "변호사", "법무사", "법무법인"}
KEEP = ("published", "approved", "review", "generated", "noindex", "retired")  # 생성기가 상태를 건드리지 않는 콤보
DIMS = ("topic", "intent", "situation", "persona", "region", "alias")
REGION_TYPES = ("region_topic", "region_topic_intent", "court_alias")
PAGE_TYPES = {  # 차원 집합 → page_type
    frozenset(["topic"]): "topic_hub", frozenset(["topic", "intent"]): "topic_intent",
    frozenset(["topic", "situation"]): "topic_situation", frozenset(["topic", "persona"]): "topic_persona",
    frozenset(["situation", "persona"]): "situation_persona", frozenset(["region", "topic"]): "region_topic",
    frozenset(["region", "topic", "intent"]): "region_topic_intent", frozenset(["alias", "topic"]): "court_alias",
}


def norm(s):
    return re.sub(r"[\s·\-]+", "", s or "").lower()


class Space:
    """버티컬 하나의 키워드 공간: 사전(용어·동의어·지역)과 현재 콤보."""

    def __init__(self, vertical):
        self.v = vertical
        self.terms = {t["id"]: t for t in ops.terms(vertical["id"])}
        self.by_dim = defaultdict(list)
        for t in self.terms.values():
            self.by_dim[t["dim"]].append(t)
        self.rep = next(t for t in self.by_dim["topic"] if t["slug"] == vertical["slug"])  # 버티컬 대표 topic
        self.regions = {r["id"]: r for r in ops.regions_upto(3)}
        self.kids = defaultdict(int)
        for r in self.regions.values():
            self.kids[r["parent_id"]] += 1
        self.aliases = ops.aliases("court")
        # site_settings.require_volume = "0" 이면 검색량 없이도 모든 콤보를 독립 페이지 후보(draft)로 둔다(web_Agency 식 템플릿 큐 운영).
        # 스펙 8.4(a)에서 벗어나는 운영 선택이라 기본값은 "1"(검색량 또는 실데이터 2블록 필요).
        self.require_volume = dbq.settings(vertical["site_id"]).get("require_volume", "1") != "0"
        self.lex = {}
        for t in self.terms.values():
            self.lex[norm(t["term"])] = (t["dim"], t)
        for s in ops.synonyms(vertical["id"]):
            self.lex.setdefault(norm(s["variant"]), (self.terms[s["term_id"]]["dim"], self.terms[s["term_id"]]))
        shorts = defaultdict(list)
        for r in self.regions.values():
            self.lex[norm(r["path_slug"])] = self.lex[norm(r["path_name"])] = ("region", r)
            shorts[norm(r["short_name"])].append(r)
        for name, rs in shorts.items():  # "부평구"처럼 전국에서 하나뿐인 이름만 단독 인식
            if len(rs) == 1:
                self.lex.setdefault(name, ("region", rs[0]))
        self.max_len = max(map(len, self.lex))
        self.combos = {self.key_of_row(c): c for c in ops.all_combos(vertical["id"])}

    # ── 정규화·태깅 ──
    def canon(self, term):
        return self.terms.get(term["canonical_term_id"], term) if term and term.get("canonical_term_id") else term

    def parse(self, keyword):
        """키워드 문자열 → {차원: 항목}. 사전에 없는 조각이 있거나 같은 차원이 두 번 나오면 None."""
        s, pos, dims = norm(keyword), 0, {}
        while pos < len(s):
            for n in range(min(self.max_len, len(s) - pos), 0, -1):
                hit = self.lex.get(s[pos:pos + n])
                if hit:
                    break
            else:
                return None
            if hit[0] in dims:
                return None
            dims[hit[0]], pos = hit[1], pos + n
        return dims or None

    def classify(self, dims):
        """대표 topic 을 채운 뒤 page_type 결정. 지원하지 않는 조합(지역×상황 등 3중 조합)은 None."""
        d = dict(dims)
        if "topic" not in d and set(d) != {"situation", "persona"}:
            d["topic"] = self.rep
        page_type = PAGE_TYPES.get(frozenset(d))
        if page_type == "topic_hub" and d["topic"]["id"] == self.rep["id"]:
            page_type = "vertical_hub"
        return (page_type, d) if page_type else (None, d)

    @staticmethod
    def key(page_type, d):
        return (page_type, *[d[k]["id"] if d.get(k) else None for k in DIMS])

    @staticmethod
    def key_of_row(c):
        return (c["page_type"], c["topic_id"], c["intent_id"], c["situation_id"], c["persona_id"], c["region_id"], c["alias_id"])

    def canon_key(self, page_type, d):
        return self.key(page_type, {k: (self.canon(v) if k in ("topic", "intent", "situation", "persona") else v) for k, v in d.items()})

    # ── 콤보 사양 ──
    def url(self, page_type, d):
        base, t = self.v["url_base"], d.get("topic")
        suffix = "" if t and t["id"] == self.rep["id"] else f"-{t['slug']}" if t else ""
        path = {
            "topic_intent": lambda: f"/{t['slug']}-{d['intent']['slug']}/",
            "topic_situation": lambda: f"/상황/{d['situation']['slug']}-{t['slug']}/",
            "topic_persona": lambda: f"/대상/{d['persona']['slug']}-{t['slug']}/",
            "situation_persona": lambda: f"/상황/{d['situation']['slug']}-{d['persona']['slug']}/",
            "region_topic": lambda: f"/지역/{d['region']['path_slug']}{suffix}/",
            "region_topic_intent": lambda: f"/지역/{d['region']['path_slug']}-{t['slug']}-{d['intent']['slug']}/",
            "court_alias": lambda: f"/법원/{d['alias']['slug']}{suffix}/",
        }[page_type]()
        return (base + path).lower()

    def keyword(self, d):
        parts = [d["region"]["path_slug"].replace("-", " ") if d.get("region") else None, d["alias"]["name"] if d.get("alias") else None]
        parts += [d[k]["term"] for k in ("situation", "persona", "topic", "intent") if d.get(k)]
        return " ".join(p for p in parts if p)

    def parent_key(self, page_type, d):
        """8.4: 가장 구체적인 차원 하나를 뺀 콤보."""
        topic = d.get("topic") or self.rep
        hub = ("vertical_hub" if topic["id"] == self.rep["id"] else "topic_hub", {"topic": topic})
        if page_type == "situation_persona":
            return self.key("topic_situation", {"topic": self.rep, "situation": d["situation"]})
        if page_type == "region_topic_intent":
            return self.key("region_topic", {"region": d["region"], "topic": topic})
        if page_type == "court_alias":
            return self.key("region_topic", {"region": self.regions[d["alias"]["region_id"]], "topic": topic})
        if page_type == "region_topic" and d["region"]["parent_id"]:
            return self.key("region_topic", {"region": self.regions[d["region"]["parent_id"]], "topic": topic})
        return self.key(*hub)

    def specs(self):
        """8.3 계층표의 조합. 부모가 먼저 나오도록 순서를 지킨다."""
        T, by = self.by_dim["topic"], self.by_dim
        intents = {t["term"]: t for t in by["intent"]}
        for t in T:
            for i in by["intent"]:
                yield "topic_intent", {"topic": t, "intent": i}
        for t in T:
            for s in by["situation"]:
                yield "topic_situation", {"topic": t, "situation": s}
        for t in T:
            for p in by["persona"]:
                yield "topic_persona", {"topic": t, "persona": p}
        for s in by["situation"]:
            for p in by["persona"]:
                yield "situation_persona", {"situation": s, "persona": p}
        regions = sorted(self.regions.values(), key=lambda r: (r["level"], r["sort_order"]))
        for r in regions:
            for t in (T if r["level"] == 1 else T[:SGG_TOPIC_COUNT]):
                yield "region_topic", {"region": r, "topic": t}
        for r in regions:
            for t in (T if r["level"] == 1 else T[:SGG_TOPIC_COUNT]):
                for name in (SIDO_INTENTS if r["level"] == 1 else SGG_INTENTS):
                    if name in intents:
                        yield "region_topic_intent", {"region": r, "topic": t, "intent": intents[name]}
        for a in self.aliases:
            for t in T[:SGG_TOPIC_COUNT]:
                yield "court_alias", {"alias": a, "topic": t}

    # ── 판단 ──
    def has_court(self, region):
        while region and not region["court_name"]:
            region = self.regions.get(region["parent_id"])
        return bool(region)

    def independent(self, page_type, d, volume):
        """8.4: (a) 검색량 임계값 이상 또는 (b) 콤보 전용 실데이터 블록 2개 이상. require_volume=0 이면 전부 후보."""
        if not self.require_volume:
            return True
        if volume >= (VOLUME_MIN_REGION if page_type in REGION_TYPES else VOLUME_MIN):
            return True
        if page_type == "region_topic":
            return self.has_court(d["region"]) and self.kids[d["region"]["id"]] > 0
        return page_type == "court_alias"

    def priority(self, d, volume, competition, payout_norm):
        commercial = 1.0 if d.get("intent") and self.canon(d["intent"])["term"] in COMMERCIAL else 0.0
        return round(math.log(volume + 1) + 0.5 * (1 - float(competition or 0.5)) + 0.5 * payout_norm + 0.5 * commercial, 3)


def rebuild(site, vertical):
    """콤보를 사양대로 만들고(없는 것만), draft/merged 콤보의 상태·부모·대표·우선순위를 다시 계산한다. 멱등."""
    sp = Space(vertical)
    payout_norm = 1.0 if ops.max_payout(vertical["id"]) else 0.0
    counts, specs = defaultdict(int), list(sp.specs())
    by_url = {c["url_path"]: c for c in sp.combos.values() if c["url_path"]}
    for page_type, d in specs:  # 1) 없는 콤보 생성
        key, url = sp.key(page_type, d), sp.url(page_type, d)
        if key not in sp.combos and url in by_url:
            # 같은 URL = 같은 검색 의도(예: topic "개인회생 비용" 과 개인회생×비용). 먼저 있던 콤보를 그대로 쓴다
            sp.combos[key] = by_url[url]
            counts["same_url"] += 1
        elif key not in sp.combos:
            cid = ops.insert_combo(site_id=site["id"], vertical_id=vertical["id"], page_type=page_type, primary_keyword=sp.keyword(d),
                                   url_path=url, status="draft", **{f"{k}_id": v["id"] for k, v in d.items()})
            sp.combos[key] = by_url[url] = dbq.combo(cid)
            counts["created"] += 1
    for page_type, d in specs:  # 2) 상태·부모·카니벌 그룹·우선순위
        row, rep = sp.combos[sp.key(page_type, d)], sp.combos[sp.canon_key(page_type, d)]
        if row["page_type"] != page_type:  # 다른 사양이 같은 URL의 기존 콤보로 흡수된 경우: 그 콤보는 자기 사양에서 평가된다
            continue
        is_rep = rep["id"] == row["id"]
        if not is_rep and not rep["cannibal_group_id"]:  # 동의어 변형이 있으면 대표에도 그룹 표시
            ops.set_combo(rep["id"], cannibal_group_id=rep["id"], is_group_rep=1)
            rep["cannibal_group_id"] = rep["id"]
        if row["status"] in KEEP:
            continue
        if is_rep:
            parent = sp.combos.get(sp.parent_key(page_type, d))
            status = "draft" if sp.independent(page_type, d, row["volume_total"] or 0) else "merged"
        else:  # 대표 콤보로 301
            parent, status = rep, "merged"
        ops.set_combo(row["id"], status=status, parent_combo_id=parent and parent["id"], is_group_rep=int(is_rep),
                      cannibal_group_id=row["cannibal_group_id"] if is_rep else rep["id"],
                      priority_score=sp.priority(d, row["volume_total"] or 0, row["competition"], payout_norm))
        counts[status] += 1
    return dict(counts)


def ingest(site, vertical, rows):
    """[{keyword, pc, mobile, competition}] → 용어·콤보 검색량 반영. 태깅 못 한 키워드는 검수 큐(job_queue: kw_review)."""
    sp = Space(vertical)
    volume, comp, counts = defaultdict(int), {}, defaultdict(int)
    for r in rows:
        total = int(r.get("pc") or 0) + int(r.get("mobile") or 0)
        dims = sp.parse(r["keyword"])
        if not dims:
            ops.enqueue("kw_review", {"keyword": r["keyword"], "volume": total})  # 사전에 없는 표현: 사람이 용어·동의어로 등록
            counts["unparsed"] += 1
            continue
        if len(dims) == 1 and "region" not in dims:
            term = next(iter(dims.values()))
            ops.update_term_metrics(term["id"], r.get("pc") or 0, r.get("mobile") or 0, r.get("competition"))
        page_type, d = sp.classify(dims)
        if not page_type:
            counts["unsupported"] += 1
            continue
        key = sp.canon_key(page_type, d)  # 동의어 변형의 검색량은 대표 콤보에 합산
        volume[key] += total
        comp[key] = max(comp.get(key, 0), float(r.get("competition") or 0))
        counts["matched"] += 1
    for key, total in volume.items():
        row = sp.combos.get(key)
        if row:
            ops.set_combo(row["id"], volume_total=total, competition=comp[key])
    return dict(counts)


def fetch_naver(hints):
    """네이버 검색광고 API 키워드도구. 환경변수 NAVER_AD_API_KEY / NAVER_AD_SECRET / NAVER_AD_CUSTOMER_ID 필요.

    필드명·쿼터는 네이버 문서 기준으로 작성했으며 실제 키로 호출해 확인하지 못했다(스펙 8.6 '확인 필요').
    """
    key, secret, customer = (os.environ.get(k) for k in ("NAVER_AD_API_KEY", "NAVER_AD_SECRET", "NAVER_AD_CUSTOMER_ID"))
    if not (key and secret and customer):
        raise RuntimeError("NAVER_AD_API_KEY / NAVER_AD_SECRET / NAVER_AD_CUSTOMER_ID 가 설정되지 않았습니다")
    ts, uri = str(int(time.time() * 1000)), "/keywordstool"
    sig = base64.b64encode(hmac.new(secret.encode(), f"{ts}.GET.{uri}".encode(), hashlib.sha256).digest()).decode()
    req = urllib.request.Request(
        f"https://api.searchad.naver.com{uri}?hintKeywords={quote(','.join(norm(h) for h in hints[:5]))}&showDetail=1",
        headers={"X-Timestamp": ts, "X-API-KEY": key, "X-Customer": customer, "X-Signature": sig})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    num = lambda v: 5 if isinstance(v, str) else int(v or 0)  # noqa: E731  "< 10" 은 5로 본다
    level = {"낮음": 0.2, "중간": 0.5, "높음": 0.8}
    return [{"keyword": k["relKeyword"], "pc": num(k.get("monthlyPcQcCnt")), "mobile": num(k.get("monthlyMobileQcCnt")),
             "competition": level.get(k.get("compIdx"), 0.5)} for k in data.get("keywordList", [])]
