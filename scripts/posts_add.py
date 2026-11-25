"""검색량으로 고른 질문 글 주제를 draft 콤보로 등록한다(이미 있는 주제·주소는 건너뜀). python scripts/posts_add.py [--dry]"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import create_app  # noqa: E402
import db  # noqa: E402
from db import ops, queries as dbq  # noqa: E402

TOPICS = {
    "dui": """음주운전 벌금|음주운전 처벌|음주운전 교육|음주운전 수치|음주운전 면허취소 기간|면허정지 벌점|음주운전 사고|음주운전 2회|무면허 음주운전|윤창호법|음주운전 구속
음주운전 구속|공무원 음주운전|음주운전 집행유예|음주운전 면허취소 벌금|음주운전 뺑소니|음주운전 면허취소 구제|음주운전 반성문|음주운전 3회|음주운전 초범|음주운전 기소유예
음주운전 재범|음주운전 방조죄|음주운전 실형|공무원 음주운전 징계|자전거 음주운전|음주운전 양형자료|음주운전 기준|음주운전 초범 벌금|음주운전 탄원서|음주운전 삼진아웃
면허정지 교육|음주운전 사망사고|전동킥보드 음주운전|음주운전 구공판|주차장 음주운전|음주운전 이의신청|음주운전 재판|음주사고 합의금|음주운전 항소|음주운전 행정처분
음주운전 면허정지|음주운전 사면|오토바이 음주운전|음주운전 무혐의|음주운전 행정소송|음주운전 형량|음주운전 대물사고|음주운전 근절 서약서|음주운전 보험처리|음주사고 면책금
집행유예 중 음주운전|음주운전 누범기간|음주단속 기준|교사 음주운전|음주운전 벌점|숙취운전 단속|음주운전 재범방지 교육|음주운전 불구속 구공판|음주측정거부 처벌|음주운전 합의""",
    "baby": """어린이보험|태아보험 다이렉트|출산보험|태아보험 산모특약|임신출산보험|비갱신 어린이보험|태아 실비보험|임신보험|쌍둥이 태아보험
태아보험 필수특약|임산부보험|어린이보험 나이|태아보험 30세 100세|태아보험 100세만기|산모보험|태아보험 어린이보험 차이|태아보험 30세만기|태아보험 설계사
태아보험 보장내용|태아보험 만기|어린이보험 가입연령|임신준비 보험|태아보험 설계|유산방지주사 태아보험|시험관 태아보험|시험관 쌍둥이 태아보험|어린이보험 실비
어린이보험 100세만기|태아 실손보험|태아보험 시기|태아보험 금액|태아보험 필수|태아보험 실손|어린이보험 가입|태아보험 혜택""",
}
norm = lambda s: re.sub(r"\s+", "", s)  # noqa: E731


def main(dry):
    for site in dbq.sites():
        label = site["host"].split(".")[0]
        if label not in TOPICS:
            continue
        v = next(iter(dbq.verticals(site["id"])))
        hubs = db.q("SELECT id, topic_id, primary_keyword, page_type FROM keyword_combos WHERE site_id=%s AND page_type IN ('vertical_hub','topic_hub')", (site["id"],))
        have = {norm(r["primary_keyword"]) for r in db.q("SELECT primary_keyword FROM keyword_combos WHERE site_id=%s AND page_type IN ('question','topic_hub','vertical_hub')", (site["id"],))}
        added, skip = [], []
        for kw in dict.fromkeys(k.strip() for k in re.split(r"[|\n]", TOPICS[label]) if k.strip()):
            url = f"{v['url_base']}/qna/{'-'.join(kw.split())}/"
            if norm(kw) in have or dbq.combo_by_url(site["id"], url):
                skip.append(kw)
                continue
            toks = set(kw.split())
            parent = max((h for h in hubs if h["page_type"] == "topic_hub" and set(h["primary_keyword"].split()) <= toks),
                         key=lambda h: len(h["primary_keyword"]), default=None) or next(h for h in hubs if h["page_type"] == "vertical_hub")
            if not dry:
                ops.insert_combo(site_id=site["id"], vertical_id=v["id"], page_type="question", topic_id=parent["topic_id"], primary_keyword=kw,
                                 url_path=url, parent_combo_id=parent["id"], status="draft", priority_score=45)  # 검색량 확인 주제: 기존 글(40 이하)보다 먼저 공개
            added.append(kw)
        print(f"[{label}] 추가 {len(added)} · 이미 있음 {len(skip)}: {skip}")


if __name__ == "__main__":
    with create_app().app_context():
        main("--dry" in sys.argv)
