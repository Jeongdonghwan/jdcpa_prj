# 세부 안내 페이지 ({{ combo.page_type }})
핵심 키워드 "{{ combo.primary_keyword }}"로 검색한 사람을 위한 페이지를 씁니다.
{% if topic %}- 주제: {{ topic.term }}
{% endif %}{% if intent %}- 알고 싶은 것: {{ intent.term }}
{% endif %}{% if situation %}- 상황: {{ situation.term }}
{% endif %}{% if persona %}- 대상: {{ persona.term }}
{% endif %}이 조합에만 해당하는 내용(이 상황·대상에서 달라지는 점, 준비할 것, 자주 막히는 지점)을 중심으로 씁니다.
## 사실 자료 (이 안에서만 수치·법령을 인용)
{% for f in facts %}- {{ f.fact_text }}{% if f.source_url %} (출처: {{ f.source_url }}){% endif %}
{% else %}- (등록된 사실 자료 없음: 수치와 조문을 쓰지 마세요)
{% endfor %}
{% if region %}## 지역 자료
- 지역: {{ region.path_name }}
{% if court %}- 관할 법원: {{ court.court_name }}{% if court.court_address %}, {{ court.court_address }}{% endif %}{% if court.court_phone %}, {{ court.court_phone }}{% endif %}
{% endif %}{% if region_children %}- 하위 행정구역 {{ region_children|length }}곳: {{ region_children|map(attribute="short_name")|join(", ") }}
{% endif %}{% for f in region_facts %}- {{ f.key }}: {{ f.value }}{% if f.as_of %} ({{ f.as_of }} 기준){% endif %}
{% endfor %}{% for p in region_programs %}- {{ p.kind or "지원 기관" }}: {{ p.name }}{% if p.address %}, {{ p.address }}{% endif %}
{% endfor %}{% endif %}
## 규칙
- 이 사이트는 광고 제휴 상담처를 소개하는 안내 사이트입니다. 법률 자문처럼 쓰지 말고 일반 정보로 씁니다.
- "~하면 됩니다" 같은 단정 대신 "~한 경우가 있습니다", "~인지 확인이 필요합니다"처럼 씁니다.
- 다음 표현은 쓰지 않습니다: {{ forbidden|join(", ") }}
- 위 자료에 없는 금액, 기간, 조문 번호, 통계는 쓰지 않습니다. 지어낸 사례나 후기도 쓰지 않습니다.
- 다른 페이지와 문장이 겹치지 않도록 이 페이지의 주제와 지역에 맞는 내용으로 씁니다.
{% if feedback %}- {{ feedback }} 이번에는 이 문제를 피해 새로 쓰세요.
{% endif %}
## 출력 형식 (JSON 객체 하나만)
{"h1_candidates": ["제목 후보 3개, 각 40자 이내"],
 "meta_description": "155자 이내 설명",
 "blocks": [
   {"slot": "summary", "html": "<ul><li>핵심 요약 3줄</li>...</ul>"},
   {"slot": "intro", "html": "<h2>질문형 소제목</h2><p>첫 문단은 2~3문장으로 바로 답합니다.</p>"},
   {"slot": "body", "html": "<h2>...</h2> 소제목 2~3개, 표(table)나 번호 목록(ol)을 한 번 이상 사용"}
 ],
 "faqs": [{"q": "질문", "a": "답변(HTML 없이 문장으로)"}],
 "internal_link_suggestions": ["함께 보면 좋은 주제 키워드"]}
faqs는 4개, 본문 전체는 {{ "1,200~1,800자" if combo.page_type in ("vertical_hub", "topic_hub") else "700~1,100자" }}로 씁니다.
