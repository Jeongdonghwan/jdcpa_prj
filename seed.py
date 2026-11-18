"""시드: 스키마 + 지역(전국 시도·시군구, 인천은 동까지) + 개인회생 버티컬 + 임시 네트워크/오퍼 + 콤보·콘텐츠 → 게이트 통과분 publish.

    python seed.py --reset     # leadsite DB를 지우고 처음부터 (개발용)

사이트명·운영사·네트워크·오퍼는 전부 임시값이다. 문구는 모두 새로 쓴 것이며 참조 사이트 문장을 쓰지 않는다.
"""
import csv
import json
import sys
from pathlib import Path

from pymysql.constants import CLIENT

import db
from app import create_app
from db import queries as dbq
import pipeline
from db import ops
from engine import gate

ROOT = Path(__file__).parent
LAW = "https://www.law.go.kr/법령/채무자회생및파산에관한법률"
J = lambda v: json.dumps(v, ensure_ascii=False)  # noqa: E731

SETTINGS = {"op_name": "미정(임시)", "op_ceo": "미정(임시)", "op_bizno": "미정(임시)", "op_address": "미정(임시)", "op_email": "미정(임시)"}

DIMENSIONS = ["topic", "intent", "situation", "persona", "region", "question", "entity"]

# (term, 정의, 확인할 점 3개) — 스펙 8.2의 topic 목록(실제로는 17개)
TOPICS = [
    ("개인회생", "개인회생은 일정한 소득이 있는 개인이 법원이 인가한 변제계획에 따라 정해진 기간 동안 빚의 일부를 나누어 갚고, 계획을 끝까지 이행하면 남은 채무의 면책을 받을 수 있도록 한 법원 절차입니다.",
     ["급여, 영업 소득처럼 앞으로도 계속 들어올 소득이 있는지", "담보가 있는 채무와 없는 채무가 각각 법에서 정한 한도 안에 있는지", "보유 재산을 처분했을 때의 가치보다 많이 갚는 계획을 세울 수 있는지"]),
    ("개인파산", "개인파산은 가진 재산과 소득으로는 빚을 갚을 수 없는 상태에 이른 개인이 법원에 신청하는 절차로, 재산이 있으면 채권자에게 나누어 준 뒤 이어지는 면책 절차에서 남은 채무의 책임을 벗을 수 있는지 판단받습니다.",
     ["현재 소득으로 생계를 유지하고 나면 갚을 여력이 남는지", "최근에 처분하거나 가족에게 넘긴 재산이 있는지", "파산 선고로 제한되는 자격이나 직업에 해당하는지"]),
    ("면책", "면책은 법원의 결정으로 남은 채무를 갚을 책임에서 벗어나는 것을 말합니다. 개인회생에서는 변제계획을 모두 이행한 뒤에, 개인파산에서는 면책 심리를 거쳐 결정됩니다.",
     ["세금, 벌금, 양육비처럼 면책되지 않는 채무가 섞여 있는지", "재산을 숨기거나 허위로 적은 내용이 없는지", "채권자 목록에서 빠진 채권자가 없는지"]),
    ("변제계획", "변제계획은 개인회생에서 소득 중 생계비를 뺀 금액을 얼마 동안, 어떤 방식으로 채권자에게 나누어 갚을지 적은 계획입니다. 법원의 인가를 받아야 효력이 생깁니다.",
     ["부양가족 수와 생계비가 실제 생활과 맞게 반영되었는지", "갚는 총액이 재산을 처분했을 때의 가치보다 적지 않은지", "매달 납부할 금액을 계획 기간 내내 감당할 수 있는지"]),
    ("채무조정", "채무조정은 법원 절차와 별개로 신용회복위원회를 통해 금융회사와 상환 조건을 다시 정하는 제도입니다. 연체 기간과 상황에 따라 이용할 수 있는 프로그램이 나뉩니다.",
     ["내 채권자가 신용회복위원회 협약에 가입한 금융회사인지", "연체 기간이 어느 프로그램의 대상에 해당하는지", "법원의 개인회생·개인파산과 비교했을 때 어느 쪽이 상황에 맞는지"]),
    ("개인회생 기각", "개인회생 기각은 신청이 법에서 정한 요건을 갖추지 못했거나, 법원이 요구한 보완을 기한 안에 하지 않았을 때 법원이 절차를 시작하지 않기로 하는 결정입니다.",
     ["법원의 보정 요구에 기한 안에 답했는지", "소득과 재산, 채권자 목록을 빠짐없이 사실대로 적었는지", "기각 결정문에 적힌 사유가 무엇인지"]),
    ("개인회생 폐지", "개인회생 폐지는 절차가 시작된 뒤에 변제계획이 인가되지 못하거나, 인가 후 변제금을 계속 내지 못하는 등의 사유로 법원이 절차를 끝내는 것을 말합니다.",
     ["변제금이 몇 회 밀렸는지, 밀린 사유를 소명할 수 있는지", "폐지 전에 변제계획 변경을 신청할 수 있는 상황인지", "폐지 후 채권자의 추심이 다시 시작될 수 있다는 점"]),
    ("금지명령", "금지명령은 개인회생을 신청한 뒤 절차 개시 여부가 정해지기 전까지 채권자가 새로 강제집행이나 추심을 하지 못하도록 법원에 요청하는 것입니다. 받아들일지는 법원이 사안별로 판단합니다.",
     ["신청서와 함께 낼지, 따로 낼지", "명령의 효력이 미치지 않는 채권이 있는지", "명령이 채권자에게 전달되기까지 시간이 걸릴 수 있다는 점"]),
    ("중지명령", "중지명령은 이미 진행 중인 강제집행, 가압류, 경매 같은 절차를 개인회생 개시 결정 전까지 멈춰 달라고 법원에 요청하는 것입니다.",
     ["현재 진행 중인 집행 절차의 사건번호와 법원", "급여 압류라면 적립 중인 금액이 어떻게 처리되는지", "법원이 받아들이지 않을 수도 있다는 점"]),
    ("재신청", "재신청은 개인회생이나 개인파산이 기각 또는 폐지된 뒤 다시 신청하는 것을 말합니다. 이전에 문제가 된 사유가 해소되었는지가 중요하게 다뤄집니다.",
     ["이전 사건이 끝난 사유와 결정문 내용", "그 사이 소득, 재산, 채무에 달라진 점", "이전 사건에서 낸 서류와 이번 서류의 내용이 서로 맞는지"]),
    ("변제금 감액", "변제금 감액은 변제계획이 인가된 뒤 소득이 줄거나 부양가족이 늘어나는 등 사정이 바뀌었을 때 변제계획 변경을 신청해 매달 내는 금액을 조정받는 것을 말합니다.",
     ["사정이 바뀐 것을 보여줄 자료(급여명세서, 진단서 등)가 있는지", "변경 후에도 법에서 요구하는 최소 변제 기준을 채우는지", "변경이 받아들여지기 전까지는 기존 금액을 내야 하는지"]),
    ("회생 후 신용회복", "개인회생이나 개인파산을 진행하면 그 사실이 신용정보에 일정 기간 기록됩니다. 기록이 지워진 뒤에도 금융 거래 이력을 다시 쌓는 데에는 시간이 필요합니다.",
     ["내 신용정보에 어떤 기록이 언제까지 남는지(신용정보원 등에서 조회)", "면책 후에도 남아 있는 연체 기록이 있는지", "통신요금, 공과금 등 비금융 납부 실적을 반영할 수 있는지"]),
    ("파산관재인", "파산관재인은 개인파산 사건에서 법원이 선임하는 사람으로, 신청인의 재산과 채무 경위를 조사하고 처분할 재산이 있으면 채권자에게 나누어 주는 일을 맡습니다.",
     ["관재인 선임에 필요한 비용을 법원에 미리 내야 한다는 점", "관재인이 요청하는 자료를 기한 안에 낼 수 있는지", "최근 몇 년간의 재산 변동과 계좌 거래를 설명할 수 있는지"]),
    ("개인회생 비용", "개인회생에 드는 비용은 법원에 내는 비용(인지대, 송달료 등)과 대리인을 선임할 때의 수임료로 나뉩니다. 법원 비용은 채권자 수 등에 따라, 수임료는 사무소마다 다릅니다.",
     ["법원에 내는 비용과 대리인 수임료가 구분되어 안내되는지", "수임료의 분할 납부 가능 여부와 추가 비용 조건", "법원 비용은 관할 법원 안내에서 직접 확인할 수 있다는 점"]),
    ("개인회생 서류", "개인회생을 신청할 때는 신청서와 함께 채권자 목록, 재산 목록, 수입·지출 목록, 진술서, 변제계획안을 내고, 이를 뒷받침하는 소득·재산·채무 증빙을 첨부합니다.",
     ["채권자별 부채증명서를 모두 받았는지", "소득을 증명할 자료가 근무 형태에 맞게 준비되었는지", "가족관계와 주거 관련 서류의 발급일이 너무 오래되지 않았는지"]),
    ("개인회생 자격", "개인회생은 급여나 영업으로 계속 소득을 얻을 가능성이 있는 개인이, 법에서 정한 채무 한도 안에서, 빚이 재산보다 많아 갚기 어려운 상태일 때 신청할 수 있습니다.",
     ["아르바이트, 프리랜서 소득도 계속성과 반복성이 인정될 수 있는지", "담보 채무와 무담보 채무가 각각 한도 이내인지", "최근에 생긴 채무의 비중이 지나치게 크지 않은지"]),
    ("개인회생 기간", "개인회생의 변제 기간은 법에서 원칙적으로 3년을 넘지 못하도록 정하고 있고, 특별한 사정이 있으면 5년까지 정해질 수 있습니다. 신청부터 인가까지 걸리는 기간은 법원과 사건에 따라 다릅니다.",
     ["내 경우 변제 기간이 3년보다 길어질 사정이 있는지", "신청 후 인가 전까지 내야 하는 적립금이 있는지", "보정 요구에 늦게 답하면 전체 기간이 길어진다는 점"]),
]
NO_COURT = {"채무조정", "회생 후 신용회복"}  # 법원 관할 문장이 어울리지 않는 topic
REP_TOPIC = "개인회생"
SGG_TOPICS = ["개인회생", "개인파산"]

INTENTS = ["비용", "수임료", "가격", "후기", "추천", "비교", "잘하는곳", "방법", "절차", "기간", "서류", "자격", "조건", "무료상담",
           "상담", "변호사", "법무사", "법무법인", "신청", "기각사유", "단점", "불이익"]

# (topic 또는 None=버티컬 공통, fact_key, fact_text) — 필자 지식 기준. verified_at 은 원문 대조 후 채운다.
FACTS = [
    (None, "채무 한도", "개인회생을 신청할 수 있는 채무 한도: 담보가 있는 채무 15억 원 이하, 담보가 없는 채무 10억 원 이하 (채무자 회생 및 파산에 관한 법률 제579조)"),
    (None, "변제 기간", "변제 기간은 변제 개시일부터 3년을 넘지 못하며, 특별한 사정이 있으면 5년까지 정할 수 있음 (같은 법 제611조 제5항)"),
    ("금지명령", "근거 조문", "개시 결정 전 중지명령과 금지명령의 근거 (같은 법 제593조)"),
    ("중지명령", "근거 조문", "개시 결정 전 중지명령과 금지명령의 근거 (같은 법 제593조)"),
    ("면책", "근거 조문", "개인회생의 면책 결정과 면책되지 않는 채무 (같은 법 제624조, 제625조), 개인파산의 면책되지 않는 채무 (같은 법 제566조)"),
    ("변제금 감액", "근거 조문", "인가 후 변제계획의 변경 (같은 법 제619조)"),
]

FAQS = [
    ("이 사이트에서 직접 상담을 해 주나요?", "아니요. 이 사이트는 광고 제휴 상담처를 소개하는 안내 사이트입니다. 상담과 사건 진행은 방문자가 선택한 상담처가 맡습니다."),
    ("상담처가 나열되는 순서는 어떻게 정해지나요?", "제휴 조건과 운영 기준에 따라 정해지며, 광고 수수료가 순서에 영향을 줄 수 있습니다. 순서가 상담처의 실력이나 사건 결과의 우열을 뜻하지는 않습니다."),
    ("상담 신청 버튼을 누르면 어디로 이동하나요?", "외부 상담 신청 페이지로 이동합니다. 이름과 연락처는 그 페이지의 운영 주체가 받으며, 이 사이트는 해당 정보를 받지 않습니다."),
    ("개인회생과 개인파산 중 어느 쪽이 맞는지 어떻게 알 수 있나요?", "계속 들어오는 소득이 있는지, 재산과 채무가 얼마인지, 채무가 생긴 경위가 어떤지에 따라 달라집니다. 일반 정보만으로 단정하기 어려우므로 자료를 정리해 상담에서 확인하는 것이 좋습니다."),
    ("소득이 일정하지 않아도 개인회생을 신청할 수 있나요?", "아르바이트나 프리랜서처럼 소득이 고르지 않아도, 앞으로 계속 반복해서 소득을 얻을 수 있다고 인정되면 신청이 가능한 경우가 있습니다. 인정 여부는 법원이 자료를 보고 판단합니다."),
    ("개인회생을 신청하면 추심이 바로 멈추나요?", "신청만으로 추심이 자동으로 멈추지는 않습니다. 금지명령이나 중지명령을 함께 신청해 법원이 받아들이거나, 개시 결정이 내려져야 효력이 생깁니다."),
    ("변제 기간은 얼마나 되나요?", "법에서는 변제 기간이 원칙적으로 3년을 넘지 못하도록 하고, 특별한 사정이 있으면 5년까지 정할 수 있도록 하고 있습니다. 실제 기간은 사건마다 법원이 인가한 변제계획에 따릅니다."),
    ("빚이 얼마까지 개인회생을 신청할 수 있나요?", "법에서 정한 한도는 담보가 있는 채무 15억 원 이하, 담보가 없는 채무 10억 원 이하입니다. 한도를 넘으면 다른 절차를 검토해야 합니다."),
    ("면책을 받으면 모든 빚이 없어지나요?", "아닙니다. 세금, 벌금, 양육비처럼 법에서 면책되지 않는다고 정한 채무는 남습니다. 어떤 채무가 여기에 해당하는지는 상담에서 채권자 목록을 놓고 확인해야 합니다."),
    ("상담 전에 무엇을 준비하면 좋나요?", "채권자별 채무 금액, 최근 몇 달의 소득 자료, 보유 재산과 보증금 내역을 메모해 두면 상담이 구체적으로 진행됩니다. 정확한 서류는 상담 이후에 안내받아 준비해도 됩니다."),
    ("가족의 재산도 조사 대상이 되나요?", "원칙적으로는 신청인 본인의 재산을 기준으로 합니다. 다만 배우자 명의 재산에 관한 자료 제출을 요구받는 경우가 있어, 사건별로 확인이 필요합니다."),
    ("신청 사실이 직장에 알려지나요?", "법원이 직장에 따로 알리는 절차는 일반적으로 없습니다. 다만 급여가 압류되어 있거나 회사가 채권자인 경우처럼 회사가 알게 되는 상황은 있을 수 있습니다."),
]

VERTICAL = {
    "trust_bullets": ["이 사이트는 법률 자문을 제공하지 않는 광고 안내 페이지입니다.", "상담과 신청은 방문자가 선택한 외부 상담처에서 진행됩니다.",
                      "개시·인가·면책 같은 절차의 결과는 법원이 판단하며 누구도 약속할 수 없습니다.", "대출 상품을 권유하거나 중개하지 않습니다."],
    "cautions": ["이 페이지의 내용은 일반 정보이며 개별 사건에 대한 법률 판단이 아닙니다.", "상담처 표시 순서는 광고 제휴 조건의 영향을 받을 수 있습니다.",
                 "수임료와 진행 조건은 상담처마다 다르므로 계약 전에 서면으로 확인하세요.", "법령과 법원 실무는 바뀔 수 있으니 중요한 결정 전에는 법령 원문과 법원 안내를 확인하세요.",
                 "상담 신청 시 입력하는 개인정보는 외부 신청 페이지의 운영 주체가 처리합니다."],
    "procedure_tabs": [
        {"name": "개인회생", "steps": ["채무·소득·재산 자료를 정리하고 상담받기", "신청서와 첨부 서류를 관할 법원에 접수(필요하면 금지·중지명령 함께 신청)",
                                  "법원의 보정 요구에 답하고 개시 결정 받기", "채권자집회 출석", "변제계획 인가", "인가된 계획대로 변제", "변제를 마친 뒤 면책 결정"]},
        {"name": "개인파산", "steps": ["채무·소득·재산 자료를 정리하고 상담받기", "파산·면책 신청서를 관할 법원에 접수", "파산 선고와 파산관재인 선임",
                                  "관재인의 재산·경위 조사와 채권자집회", "면책 심리", "면책 여부 결정"]},
    ],
    "personas": ["매달 소득은 있지만 원리금을 감당하기 어려운 분", "압류나 추심이 시작되어 절차를 서둘러 알아봐야 하는 분", "개인회생과 개인파산 중 무엇이 맞는지 모르는 분"],
}

# 스펙 8.2의 상황·대상 차원(Phase 4 콤보 생성용). slug 는 공백 제거
SITUATIONS = ["주식 빚", "코인 빚", "도박 빚", "사업실패", "폐업", "카드빚", "대출 돌려막기", "대부업 채무", "보증 채무", "전세보증금", "의료비",
              "학자금", "이혼", "실직", "소득 불규칙", "연체", "압류", "급여 압류", "통장 압류", "차량 보유", "주택 보유", "전세 거주", "배우자 재산"]
PERSONAS = ["직장인", "자영업자", "프리랜서", "무직", "주부", "공무원", "군인", "간호사", "배달기사", "택시기사", "대학생", "20대", "30대", "40대",
            "50대", "고령자", "사회초년생"]
INTENT_SYNONYMS = {"수임료": "비용", "가격": "비용", "조건": "자격"}   # 같은 의도 → 대표 용어(카니벌 그룹)
VARIANTS = {"주식 빚": ["주식빚", "주식 투자 빚"], "코인 빚": ["코인빚", "가상자산 빚"], "무료상담": ["무료 상담"], "잘하는곳": ["잘하는 곳"]}
SIDO_SHORT = {"서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "대전광역시": "대전", "울산광역시": "울산",
              "세종특별자치시": "세종", "경기도": "경기", "충청북도": "충북", "충청남도": "충남", "경상북도": "경북", "경상남도": "경남",
              "제주특별자치도": "제주", "강원특별자치도": "강원", "전북특별자치도": "전북", "전남광주통합특별시": "전남광주"}
# 인천 군·구 인접(지도상 경계를 맞댄 것이 분명한 쌍만). 양방향으로 넣는다
ADJACENT = [("부평구", "계양구"), ("부평구", "남동구"), ("부평구", "서해구"), ("미추홀구", "남동구"), ("미추홀구", "연수구"),
            ("미추홀구", "제물포구"), ("남동구", "연수구"), ("계양구", "서해구"), ("서해구", "검단구"), ("제물포구", "서해구")]
# 2026년 기준 중위소득(보건복지부 고시). 개인회생 생계비 = 60%
MEDIAN_INCOME = {1: 2564238, 2: 4199292, 3: 5359036, 4: 6494738, 5: 7556719, 6: 8555952}
MEDIAN_SOURCE = "https://www.bokjiro.go.kr/ssis-tbu/cms/pc/news/promotion/1308418_1118.html"
# Phase 7 버티컬: 오퍼가 확보되기 전까지 비활성(is_active=0). topic 은 스펙 8.2
EXTRA_VERTICALS = [
    ("이사청소", "이사·청소", "/이사청소", {"required": ["biz_name"], "badges_forbidden": ["1위", "최고", "최저가"]},
     ["포장이사", "반포장이사", "원룸이사", "용달이사", "사무실이사", "장거리이사", "보관이사", "입주청소", "이사청소", "거주청소", "사무실청소",
      "줄눈 시공", "새집증후군", "곰팡이 제거", "에어컨 청소", "매트리스 청소", "정기 가사청소"]),
    ("인터넷", "인터넷 가입", "/인터넷", {"required": ["biz_name"], "badges_forbidden": ["1위", "최고", "최대 지원"]},
     ["인터넷", "인터넷 TV 결합", "휴대폰 결합", "통신사 변경", "사은품", "이전설치", "해지 위약금", "기가인터넷", "와이파이 공유기",
      "사업자 인터넷", "원룸 인터넷", "알뜰폰 결합"]),
    ("장기렌트", "장기렌트·리스", "/장기렌트", {"required": ["biz_name"], "badges_forbidden": ["1위", "최고", "최저가"]},
     ["장기렌트", "중고차 장기렌트", "리스", "법인 장기렌트", "개인사업자 장기렌트", "전기차 장기렌트", "인수형 반납형", "무보증 렌트",
      "선납금", "잔존가치", "주행거리 약정", "중도해지"]),
]

NETWORK = {"slug": "demo-net", "name": "임시 네트워크(더미)", "kind": "cpa_network",
           "conventions": {"click_param": "sub1", "max_sub_len": 64},
           "postback": {"enabled": True, "method": "GET", "click_id_param": "sub1", "external_id_param": "txid", "status_param": "status",
                        "amount_param": "amount", "event_param": "event", "secret_param": "key", "secret": "dev-postback-secret",
                        "status_map": {"approved": "1", "pending": "0", "rejected": "2"}},
           "csv_mapping": {"click_id": "sub1", "external_id": "txid", "status": "status", "amount": "amount", "occurred_at": "date",
                           "status_map": {"approved": "승인", "pending": "대기", "rejected": "반려"}}}
# (slug, 상호, 분야, 광고책임변호사, 단가, 가중치) — 전부 더미. 네 번째는 필수 표기 누락 → 노출되면 안 된다.
OFFERS = [
    ("demo-law-a", "임시 법률사무소 A", "개인회생·파산", "김임시", 30000, 2),
    ("demo-law-b", "임시 법무법인 B", "개인회생", "이임시", 25000, 1),
    ("demo-law-c", "임시 법률사무소 C", "개인회생·파산", "박임시", 20000, 1),
    ("demo-law-nolawyer", "임시 상담센터 D(표기 누락)", "개인회생", None, 40000, 3),
]


def slugify(s):
    return s.replace(" ", "-").lower()


def p(*paras):
    return "".join(f"<p>{x}</p>" for x in paras)


def ul(items):
    return "<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>"


def topic_blocks(topic, definition, points):
    return [
        {"slot": "summary", "html": ul([definition.split(". ")[0].rstrip(".") + ".", "아래 내용은 제도의 일반 정보이며, 내 경우에 해당하는지는 상담에서 확인해야 합니다.",
                                        "소개된 상담처는 광고 제휴처이고 상담 신청은 각 상담처의 외부 페이지에서 진행됩니다."])},
        {"slot": "intro", "html": f"<h2>{topic}, 어떤 제도인가요</h2>" + p(definition)},
        {"slot": "body", "html": f"<h2>{topic} 알아볼 때 확인할 점</h2>" + ul(points)},
    ]


def region_blocks(region, topic, definition, points, court_name):
    full, short = region["path_name"], region["short_name"]
    first = (f"{full}에 주소를 둔 분의 {topic} 사건은 일반적으로 {court_name}이 관할합니다. 근무지 기준으로 접수할 수 있는 경우도 있어 접수 전에 확인이 필요합니다."
             if topic not in NO_COURT else f"{full}에서 {topic} 정보를 찾는 분을 위한 일반 안내 페이지입니다.")
    return [
        {"slot": "summary", "html": ul([first, definition.split(". ")[0].rstrip(".") + ".", "소개된 상담처는 광고 제휴처이고 상담 신청은 각 상담처의 외부 페이지에서 진행됩니다."])},
        {"slot": "intro", "html": f"<h2>{short}에서 {topic} 알아보기</h2>" + p(
            definition, f"아래 지역 정보에서 {short}의 관할 법원과 행정구역을 확인하고, 제휴 상담처 가운데 상담을 신청할 곳을 고를 수 있습니다. 상담처는 {short} 밖에 있을 수 있으며 전화나 온라인 상담 가능 여부는 상담처마다 다릅니다.")},
        {"slot": "body", "html": f"<h2>{short} 거주자가 {topic} 상담 전에 확인할 점</h2>" + ul(points)},
    ]


def main():
    app = create_app()
    cfg = app.config
    raw = db.connect(cfg, database=False, client_flag=CLIENT.MULTI_STATEMENTS)
    with raw.cursor() as cur:
        if "--reset" in sys.argv:
            cur.execute(f"DROP DATABASE IF EXISTS `{cfg['DB_NAME']}`")
        cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name='sites'", (cfg["DB_NAME"],))
        if cur.fetchone():
            sys.exit("이미 시드된 DB입니다. 다시 만들려면 --reset 을 붙이세요.")
        cur.execute((ROOT / "db" / "schema.sql").read_text("utf-8"))  # 스펙 10.1 DDL 그대로
        while cur.nextset():
            pass
    raw.close()

    with app.app_context():
        ex, q1 = db.ex, db.q1
        host = cfg["BASE_URL"].split("://", 1)[1]
        ex("INSERT INTO sites (id, host, name) VALUES (1, %s, %s)", (host, "리드사이트(임시)"))
        for k, v in SETTINGS.items():
            ex("INSERT INTO site_settings VALUES (1, %s, %s)", (k, v))
        for i, d in enumerate(DIMENSIONS, 1):
            ex("INSERT INTO keyword_dimensions VALUES (%s, %s, %s, %s)", (i, d, d, i))

        # 지역: 행정표준코드관리시스템(code.go.kr) 법정동코드 전체자료에서 '존재'인 시도·시군구(전국)와 인천 읍면동만 추린 파일
        by_name, n_regions = {}, 0
        with open(ROOT / "data" / "regions.tsv", encoding="utf-8") as f:
            for n, row in enumerate(csv.DictReader(f, delimiter="\t")):
                code, names = row["법정동코드"], row["법정동명"].split()
                is_dong = code[5:] != "00000"
                level = 4 if is_dong else {1: 1, 2: 2, 3: 3}[len(names)]   # 1 시도 2 시군구 3 일반구 4 읍면동
                short_sido = SIDO_SHORT[names[0]]                            # 새 시도 명칭이 생기면 여기서 KeyError 로 알린다
                short = short_sido if level == 1 else names[-1]
                incheon = short_sido == "인천"
                by_name[" ".join(names)] = ex(
                    """INSERT INTO regions (parent_id, level, code, slug, short_name, full_name, path_slug, path_name, publish_stage, sort_order)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (by_name.get(" ".join(names[:-1])), level, code, short, short, names[-1], "-".join([short_sido] + names[1:]),
                     " ".join(names), int(incheon and level < 4), n))
                n_regions += 1
        sido_id = by_name["인천광역시"]
        # 법원명·URL만 시드. 주소·전화는 공식 출처로 확인하지 못해 비워 둔다(관리자 > 지역 또는 CSV 적재 court)
        ex("UPDATE regions SET court_name='인천지방법원', court_url='https://incheon.scourt.go.kr' WHERE id=%s", (sido_id,))
        ex("INSERT INTO region_aliases (region_id, alias_type, name, slug) VALUES (%s,'court','인천지방법원','인천지방법원')", (sido_id,))
        sido = dbq.region(sido_id)
        sggs = dbq.region_children(sido_id)
        sgg_id = {r["short_name"]: r["id"] for r in sggs}
        for x, y in ADJACENT:
            ex("INSERT INTO region_adjacency VALUES (%s,%s),(%s,%s)", (sgg_id[x], sgg_id[y], sgg_id[y], sgg_id[x]))
        ex("INSERT INTO authors (slug, name, role, bio) VALUES ('editor', '편집팀(임시)', '콘텐츠 편집', %s)",
           ("제도 안내 글을 쓰고 법령·공공자료와 대조해 고치는 운영사 편집팀입니다. 변호사가 아니며 법률 자문을 하지 않습니다.",))
        ops.set_setting(1, "weekly_publish_cap", "100")
        ops.set_setting(1, "require_volume", "1")  # 0 이면 검색량 없이 모든 콤보를 공개 후보로(스펙 8.4a 완화)

        vid = ex("""INSERT INTO verticals (site_id, slug, name, short_label, url_base, compliance_schema, trust_bullets, cautions, procedure_tabs, personas, keyword_template)
                    VALUES (1,'개인회생','개인회생·파산','개인회생','/개인회생',%s,%s,%s,%s,%s,%s)""",
                 (J({"required": ["ad_lawyer"], "badges_forbidden": ["1위", "최고"]}), J(VERTICAL["trust_bullets"]), J(VERTICAL["cautions"]),
                  J(VERTICAL["procedure_tabs"]), J(VERTICAL["personas"]), "{region} {topic}, {topic} 상담, 개인회생, 개인파산"))

        topic_ids = {}
        for term, _d, _p in TOPICS:
            topic_ids[term] = ex("INSERT INTO keyword_terms (vertical_id, dimension_id, term, slug) VALUES (%s,1,%s,%s)", (vid, term, slugify(term)))
        term_id = {}
        for dim, terms in ((2, INTENTS), (3, SITUATIONS), (4, PERSONAS)):  # 페이지는 콤보 생성기(flask jobs combos)가 만든다
            for term in terms:
                term_id[term] = ex("INSERT INTO keyword_terms (vertical_id, dimension_id, term, slug) VALUES (%s,%s,%s,%s)",
                                   (vid, dim, term, term.replace(" ", "")))
        for variant, canonical in INTENT_SYNONYMS.items():
            ex("UPDATE keyword_terms SET canonical_term_id=%s WHERE id=%s", (term_id[canonical], term_id[variant]))
        for term, variants in VARIANTS.items():
            for v in variants:
                ex("INSERT INTO keyword_synonyms VALUES (%s,%s,'seed')", (term_id[term], v))
        for n, amount in MEDIAN_INCOME.items():  # 변제금 계산기용: min=60%(생계비), max=기준 중위소득
            ex("INSERT INTO price_ranges (vertical_id, item, min_amount, max_amount, unit, source, as_of) VALUES (%s,%s,%s,%s,'원',%s,'2026-01-01')",
               (vid, f"생계비 {n}인", int(amount * 0.6), amount, MEDIAN_SOURCE))
        for slug, name, base, schema, topics_ in EXTRA_VERTICALS:
            v2 = ex("INSERT INTO verticals (site_id, slug, name, url_base, compliance_schema, is_active) VALUES (1,%s,%s,%s,%s,0)",
                    (slug, name, base, J(schema)))
            for t in topics_:
                ex("INSERT INTO keyword_terms (vertical_id, dimension_id, term, slug) VALUES (%s,1,%s,%s)", (v2, t, slugify(t)))
        for topic, key, text in FACTS:
            ex("INSERT INTO topic_facts (vertical_id, topic_term_id, fact_key, fact_text, source_url) VALUES (%s,%s,%s,%s,%s)",
               (vid, topic_ids.get(topic), key, text, LAW))
        for question, answer in FAQS:
            ex("INSERT INTO faq_pool (vertical_id, scope, question, answer) VALUES (%s,'vertical',%s,%s)", (vid, question, answer))

        nid = ex("INSERT INTO networks (slug, name, kind, conventions, postback, csv_mapping) VALUES (%s,%s,%s,%s,%s,%s)",
                 (NETWORK["slug"], NETWORK["name"], NETWORK["kind"], J(NETWORK["conventions"]), J(NETWORK["postback"]), J(NETWORK["csv_mapping"])))
        offer_ids = []
        for slug, adv, cat, lawyer, payout, weight in OFFERS:
            fields = {"ad_lawyer": lawyer} if lawyer else {}
            offer_ids.append(ex(
                """INSERT INTO offers (network_id, vertical_id, slug, advertiser, name, category, payout_type, payout_amount, integration_mode,
                     tracking_url_template, manual_weight, compliance_fields, compliance_ok, blurb, status)
                   VALUES (%s,%s,%s,%s,%s,%s,'CPA',%s,'redirect',%s,%s,%s,%s,%s,'active')""",
                (nid, vid, slug, adv, adv, cat, payout,
                 f"https://example.com/pt/{slug}?sub1={{click_id}}&sub2={{region_slug}}&sub3={{keyword}}", weight, J(fields), int(bool(lawyer)),
                 "임시 등록된 더미 상담처입니다. 실제 오퍼가 정해지면 관리자에서 교체하세요.")))
        ex("UPDATE verticals SET fallback_offer_id=%s, form_offer_id=%s WHERE id=%s", (offer_ids[0], offer_ids[0], vid))

        site = dbq.site()
        sep, name = site["title_sep"], site["name"]

        def add_combo(page_type, keyword, url, parent, h1, desc, blocks, topic=None, region_id=None):
            cid = ex("""INSERT INTO keyword_combos (site_id, vertical_id, page_type, topic_id, region_id, primary_keyword, url_path, parent_combo_id, status, content_version)
                        VALUES (1,%s,%s,%s,%s,%s,%s,%s,'approved',1)""", (vid, page_type, topic_ids.get(topic), region_id, keyword, url, parent))
            ex("""INSERT INTO page_contents (combo_id, version, h1, title, meta_description, blocks, model, prompt_version, status, reviewer, reviewed_at)
                  VALUES (%s,1,%s,%s,%s,%s,'seed-template','phase1','approved','seed',NOW())""", (cid, h1, h1 + sep + name, desc[:200], J(blocks)))
            return cid

        topics = {t: (d, pts) for t, d, pts in TOPICS}
        d, pts = topics[REP_TOPIC]
        hub = add_combo("vertical_hub", "개인회생·파산", "/개인회생/", None, "개인회생·파산 제도 안내와 제휴 상담처",
                        "개인회생과 개인파산의 일반 정보, 주제별·지역별 안내와 광고 제휴 상담처를 정리했습니다. 법률 자문이 아니며 상담은 외부 상담처에서 진행됩니다.",
                        topic_blocks(REP_TOPIC, d, pts), topic=REP_TOPIC)
        topic_hub = {REP_TOPIC: hub}  # 대표 topic 은 버티컬 허브와 같은 URL
        for t, (d, pts) in topics.items():
            if t != REP_TOPIC:
                topic_hub[t] = add_combo("topic_hub", t, f"/개인회생/{slugify(t)}/", hub, f"{t} 제도 안내와 제휴 상담처",
                                         f"{t}의 뜻과 알아볼 때 확인할 점, 광고 제휴 상담처를 안내합니다. 일반 정보이며 개별 사건의 판단은 상담에서 확인하세요.",
                                         topic_blocks(t, d, pts), topic=t)

        def region_combo(region, t, parent):
            d, pts = topics[t]
            suffix = "" if t == REP_TOPIC else "-" + slugify(t)
            label = region["path_slug"].replace("-", " ")
            return add_combo("region_topic", f"{label} {t}", f"/개인회생/지역/{region['path_slug']}{suffix}/", parent,
                             f"{region['path_name']}에서 {t} 상담처 찾기",
                             f"{region['path_name']} {t} 관련 관할 법원 정보와 광고 제휴 상담처를 안내합니다. 일반 정보이며 신청 가능 여부는 상담에서 확인하세요.",
                             region_blocks(region, t, d, pts, sido["court_name"]), topic=t, region_id=region["id"])

        sido_combo = {t: region_combo(sido, t, topic_hub[t]) for t in topics}      # 시도 × topic 전체
        for sgg in sggs:                                                             # 시군구 × 개인회생·개인파산
            for t in SGG_TOPICS:
                region_combo(sgg, t, sido_combo[t])

        # 채점(린트·고유성·팩트) 후 품질 게이트 통과분만 publish.
        # 내부 링크 수는 공개된 콤보끼리 세므로 일단 전부 공개로 두고 실패분을 review 로 되돌린다
        pending = db.q("SELECT * FROM keyword_combos WHERE status='approved' ORDER BY id")
        for c in pending:
            pipeline.score(c, dbq.content(c["id"], 1))
        ex("UPDATE keyword_combos SET status='published', published_at=NOW(), content_updated_at=NOW() WHERE status='approved'")
        failed = {}
        for c in pending:
            fails = gate(site, dbq.combo(c["id"]))
            if fails:
                failed[c["url_path"]] = fails
                ex("UPDATE keyword_combos SET status='review', published_at=NULL, content_updated_at=NULL WHERE id=%s", (c["id"],))
        n = q1("SELECT COUNT(*) n FROM keyword_combos WHERE status='published'")["n"]
        print(f"지역 {n_regions}개, 콤보 {len(pending)}개 중 {n}개 published, 게이트 실패 {len(failed)}개")
        reasons = {}
        for fails in failed.values():
            for f in fails:
                key = f.split(" ")[0]
                reasons[key] = reasons.get(key, 0) + 1
        print("  실패 사유:", reasons)


if __name__ == "__main__":
    main()
