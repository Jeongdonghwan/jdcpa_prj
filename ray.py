"""레이 구조(2026-11-24) — 지역 키워드 페이지(region_topic)의 화면 틀.

본문은 DB 에 승인된 글(seed-template 블록)과 업종 공통 자료(해설·확인표·유의사항·질문)를 그대로 쓰고, 새 문장을 만들지 않는다.
여기서는 그것을 레이 모범 구조로 다시 묶기만 한다(사용자 지시 2026-11-24, composer-patterns §5-1·§5-2):
  분류 줄 → h1(키워드 + 수식어) → 설명 한 문단 → "{kw} 핵심 요약"(결론 + 정보 칸 4)
  → 번호 절 6개(h2 = "{kw}: 세부 주제", 4개에 키워드 전체, 절마다 한 문단) → "{kw} 자주 묻는 질문" → 관련 링크 3묶음(15개 이하)
고르는 것은 전부 combo.id 시드라 같은 페이지는 늘 같은 결과다. 저장된 h1·title·설명·주소는 바꾸지 않는다.
"""
import hashlib
import random
import re

TAG = re.compile(r"<[^>]+>")
SENT = re.compile(r"(?<=[.!?])\s+")


def pick(seq, n, seed):
    """app._pick 과 같은 규칙(템플릿 |pick 과 같은 결과)."""
    seq = list(seq or [])
    if len(seq) <= n:
        return seq
    keep = set(random.Random(str(seed)).sample(range(len(seq)), n))
    return [x for i, x in enumerate(seq) if i in keep]


def one(options, seed):
    return options[int(hashlib.md5(str(seed).encode()).hexdigest(), 16) % len(options)]


def jo(word, a, b):
    w = (word or "").strip()
    last = w[-1] if w else ""
    return w + (a if "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 else b)


def _end(t):
    t = (t or "").strip()
    return t if not t or t.endswith((".", "?", "!")) else t + "."


def _paras(html):
    """슬롯 html → (h2 제외) 문단 글 목록."""
    html = re.sub(r"<h2>.*?</h2>", "", html or "", flags=re.S)
    return [TAG.sub("", p).strip() for p in re.findall(r"<p>(.*?)</p>", html, flags=re.S) if TAG.sub("", p).strip()]


def _li(html):
    return [TAG.sub("", x).strip() for x in re.findall(r"<li>(.*?)</li>", html or "", flags=re.S)]


def _fresh(words, kw):
    """수식어 낱말이 키워드에 이미 있으면 False(h1 같은 말 반복 금지)."""
    toks = [t for t in re.split(r"[\s·,]+", kw) if len(t) >= 2]
    return not any(t in w or w in t for w in words if len(w) >= 2 for t in toks)


def h1sub(cid, kw, children, court_name, checks, guide_title, partner):
    """h1 아래 줄: 하위 지역·관할 법원·확인 항목·해설 제목에서 3~4벌, 키워드와 겹치는 벌은 뺀다."""
    names = [c for c in children if c]
    cands = []
    if len(names) >= 2:
        r = cid % len(names)
        a, b = (names[r:] + names[:r])[:2]
        cands.append(([a, b], f"{a}·{b} 등 {len(names)}곳 기준 안내"))
    if court_name:
        cands.append(([court_name], f"{court_name} 관할 기준 확인할 점"))
    if len(checks) >= 2:
        cands.append(([checks[0], checks[1]], f"{checks[0]}·{checks[1]}부터 정리하기"))
    if guide_title and "," not in guide_title:
        cands.append(([guide_title], guide_title))
    if not cands:
        cands.append(([partner], f"{partner} 신청 전에 볼 것"))
    ok = [t for w, t in cands if _fresh(w, kw)] or [cands[-1][1]]
    return one(ok, f"{cid}|h1sub")


def fit80(sents, keep=(0,)):
    """문장 단위로 80자(공백 포함) 안에 맞춘다: 넘치면 keep 이 아닌 문장을 뒤에서부터 뺀다."""
    s = [x for x in sents if x]
    while len(" ".join(s)) > 80 and len(s) > len(keep):
        drop = max(i for i in range(len(s)) if i not in keep)
        s.pop(drop)
    return " ".join(s)


def desc(cid, kw, checks, fact, partner):
    """지역 페이지 description(45~80자, composer-patterns §5-3): 키워드 맨 앞 → 확인 항목 2~3개(번호 절에 있는 것) → 페이지 사실 → 상담."""
    c = (checks + ["", "", ""])[:3]
    first = one([f"{kw}, {jo(c[0] + '·' + c[1], '을', '를')} 먼저 확인하세요.",
                 f"{jo(kw, '은', '는')} {c[0]}·{c[1]}·{c[2]}에 따라 달라집니다." if c[2] else f"{jo(kw, '은', '는')} {c[0]}·{c[1]}에 따라 달라집니다.",
                 f"{kw}, 확인 항목 {c[0]}·{jo(c[1], '을', '를')} 정리했습니다."], f"{cid}|d1") if c[1] else f"{kw} 확인할 점을 정리했습니다."
    end = one([f"{partner} 상담 신청도 안내합니다.", f"상담은 {partner}에 신청합니다."], f"{cid}|d2")
    return fit80([first, fact, end], keep=(0, 2)) if len(f"{first} {end}") <= 80 else fit80([first, fact, end])


def desc_plain(saved, h1):
    """지역 페이지가 아닌 글: 저장된 설명을 문장 단위로 45~80자에 맞춘다. 두 문장이 넘치면 제목 + 상담 문장."""
    s = [x for x in SENT.split((saved or "").strip()) if x]
    first = s[0] if s else ""
    tails = ["확인할 조건과 상담 전에 정리할 내용을 담았습니다.", "상담 전에 확인할 점을 정리했습니다."]
    q = h1 + "?" if h1.endswith(("나요", "까요", "가요", "는지")) else _end(h1)
    # 제목(페이지마다 다름)을 앞에: 같은 첫 문장을 쓰는 형제 질문 글끼리 설명이 겹치지 않게
    for d in ([" ".join(s[:2])] if len(s) > 1 else []) + [f"{q} {t}" for t in tails] + [f"{first} {t}" for t in tails] + [first]:
        if 45 <= len(d) <= 80:
            return d
    return fit80(s) if len(first) <= 80 else first


H2 = {
    "s1": ["{kw}: {short} 지역 안내", "{kw} — {short}에서 알아볼 때", "{kw}: 이 지역 기준 안내"],
    "s2": ["{kw}: {short}의 행정구역", "{kw} — {short_wa} 가까운 지역", "{kw}: 지역 범위와 인근 안내"],
    "s3": ["{kw}: {g}", "{kw} — {g}"],
    "s4": ["{ct}", "신청 전에 정리할 항목 {n}가지", "{a}·{b}·{c} 정리하기"],
    "s5": ["이런 상황이라면", "{short}에서 알아보는 경우", "{short} 접수와 관할"],
    "s6": ["{kw}: 이용 전에 알아둘 점", "{kw} — 알아둘 점과 근거", "{kw}: 유의사항"],
}


def build(ctx):
    """engine.assemble 결과 → 레이 구조 dict. 지역 페이지가 아니면 None."""
    combo, region, v = ctx["combo"], ctx["region"], ctx["vertical"]
    if not region or combo["page_type"] != "region_topic":
        return None
    import json
    schema = json.loads(v["compliance_schema"] or "{}") if isinstance(v["compliance_schema"], str) else (v["compliance_schema"] or {})
    ui, cid = schema.get("ui") or {}, combo["id"]
    partner = ui.get("partner") or "제휴 상담처"
    kw = (ctx["content"]["h1"] or combo["primary_keyword"]).split(", ", 1)[0]
    short, full = region["short_name"], region["path_name"]
    slot = {}
    for b in ctx["blocks"]:
        slot[b["slot"]] = slot.get(b["slot"], "") + b["html"]
    intro, body, summ = _paras(slot.get("intro")), _paras(slot.get("body")), _li(slot.get("summary"))
    # 템플릿이 지역 페이지에서 고르던 부분집합과 같은 시드
    guide = pick(schema.get("guide") or [], 1, f"{cid}g")
    checks = pick(json.loads(v["issues"] or "[]") if isinstance(v["issues"], str) else (v["issues"] or []), 3, f"{cid}c")
    personas = pick(v["personas"], 2, f"{cid}p")
    notes = pick(v["cautions"], 2, f"{cid}n")
    facts = pick(ctx["topic_facts"], 1, f"{cid}t")
    court = ctx["court"] if ctx["court"] and not ui.get("no_court") else None
    court_name = (court["court_name"] + (" " + court["court_branch"] if court.get("court_branch") else "")) if court else ""
    children = [c["short_name"] for c in ctx["region_children"]]
    unit = "시·군·구" if region["level"] == 1 else ("구" if ctx["region_children"] and ctx["region_children"][0]["level"] == 3 else "법정동·읍면")
    topic = ctx["topic"]["term"] if ctx.get("topic") else v["name"]
    area = f"{short} {ui.get('area_note') or '접수 가능 여부는 신청 뒤 제휴처에서 확인합니다.'}"

    # 핵심 요약: 지역 안내 첫 줄 + 먼저 볼 점(본문 첫 문장) + 접수 + 광고 고지 — 전부 저장된 글·화면 문구
    point = body[0] if body else ""
    summary = [s for s in [summ[0] if summ else "", point, area, summ[1] if len(summ) > 1 else ""] if s]
    dl = [("지역", full),
          ("관할 법원", court_name) if court_name else (f"{unit} {len(children)}곳" if children else "상위 지역", ", ".join(children[:6]) + (" 등" if len(children) > 6 else "") if children else full.rsplit(" ", 1)[0]),
          ("주제", topic), ("신청", f"{partner} 외부 신청 페이지(광고)")]

    secs = []
    f = {"kw": kw, "short": short, "short_wa": jo(short, "과", "와")}
    # 1 지역 안내(도입·접수) 2 행정구역·인근 3 해설 1꼭지 4 확인할 항목 5 상황·관할·접수 6 유의사항·근거
    s1 = [p for p in intro if not re.search(r"\d+곳|속한|속에|들어갑니다|나뉩니다|위치를 기준", p)]
    s2 = [p for p in intro if p not in s1] + body[1:]
    if s1:
        secs.append(("s1", {}, " ".join(s1)))
    if s2:
        secs.append(("s2", {}, " ".join(s2)))
    if guide:
        secs.append(("s3", {"g": guide[0][0]}, " ".join(guide[0][1])))
    if checks:
        lead = f"{jo(kw, '을', '를')} 알아볼 때 미리 확인할 항목 {len(checks)}가지입니다."
        rows = " ".join(f"{jo(c[0], '은', '는')} {jo(c[1], '을', '를')} 봅니다. {_end(c[2])}" for c in checks)
        names = [c[0] for c in checks] + ["", "", ""]
        secs.append(("s4", {"ct": ui.get("checks_title") or "신청 전에 정리할 것", "n": len(checks), "a": names[0], "b": names[1], "c": names[2]}, lead + " " + rows))
    s5 = []
    if len(personas) >= 2:
        s5.append(f"{jo(personas[0], '이나', '나')} {jo(personas[1], '이라면', '라면')} 앞의 항목부터 정리해 두면 상담에서 설명하기 쉽습니다.")
    if court_name:
        addr = f"(주소 {court['court_address']})" if court.get("court_address") else ""
        s5.append(f"{short} 주소지 기준 일반적인 관할은 {court_name}{addr}입니다. 실제 관할은 접수 전에 법원 안내로 확인하세요.")
    for x in ctx.get("region_facts") or []:
        s5.append(f"{x['key']}: {x['value']}.")
    s5.append(area)
    secs.append(("s5", {}, " ".join(s5)))
    s6 = [_end(n) for n in notes] + [_end(x["fact_text"]) for x in facts]
    if s6:
        secs.append(("s6", {}, " ".join(s6)))
    out = []
    for i, (key, extra, text) in enumerate(secs):
        opts = H2[key]
        if key == "s4" and not extra.get("c"):
            opts = opts[:2]
        if key == "s5" and not court_name:
            opts = opts[:2]
        h = one(opts, f"{cid}|{key}").format(**f, **extra)
        out.append({"id": f"r{i + 1}", "h2": h, "text": text})
    src = next((x for x in facts if x.get("source_url")), None)

    links, seen, groups, total = ctx["links"], {combo["url_path"]}, [], 0
    for label, items, cap in ((f"{short}의 다른 주제", links.get("other_topics") or [], 5),
                              (f"{topic}, 인근 지역", (links.get("adjacent") or []) + (links.get("siblings") or []), 6),
                              (f"{topic} 상위·세부 안내", (links.get("parent") or []) + (links.get("children") or []), 4)):
        keep = []
        for l in items:
            if l["url_path"] in seen or len(keep) >= cap or total + len(keep) >= 15:
                continue
            seen.add(l["url_path"])
            keep.append(l)
        if keep:
            groups.append((label, keep))
            total += len(keep)
    return {"kw": kw, "cat": f"{v['name']} · {full.split(' ')[0]}", "h1sub": h1sub(cid, kw, children, court_name, [c[0] for c in checks],
                                                                                    guide[0][0] if guide else "", partner),
            "summary_h2": f"{kw} 핵심 요약", "summary": " ".join(_end(s) for s in summary), "dl": dl,
            "desc": desc(cid, kw, [c[0] for c in checks], f"관할 법원은 {court_name}입니다." if court_name else
                         (f"{children[0]}·{children[1]} 등 {len(children)}곳을 함께 안내합니다." if len(children) >= 2 else f"자주 묻는 질문 {len(ctx['faqs'])}개를 함께 담았습니다."), partner),
            "sections": out, "source": src, "faq_h2": f"{kw} 자주 묻는 질문", "related": groups}
