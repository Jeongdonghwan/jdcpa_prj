# 단계별 구축 계획

출처: docs/spec.md 10.6. 완료 기준은 로컬에서 검증한 것만 체크했다(2026-10-05).
체크하지 않은 항목은 코드는 있지만 외부 키·실데이터·사람 검수·배포가 있어야 끝나는 것이며, 항목마다 무엇이 필요한지 적었다.

검증 명령

```
python seed.py --reset
flask --app app jobs combos
python tests/test_phase1.py      # 12개
python tests/test_phases.py      # 21개 (Phase 2~8)
python scripts/check_sitemap.py http://127.0.0.1:5000
```

## Phase 1. 코어 + 네트워크 1 + 버티컬 1

범위
- Flask 골격(web / go / seo / admin blueprint), db/*.py SQL 함수, 10.1 DDL 전체 적재
- regions 적재: 전국 시도·시군구(Phase 4 콤보 생성용, 비공개) + 인천은 동까지. 공개 대상은 인천만
- verticals(개인회생), keyword_dimensions, keyword_terms 시드(topic 17, intent 22 — 스펙 8.2 목록의 topic은 실제 17개)
- combos: vertical_hub · topic_hub · region_topic(시도·시군구)
- networks 1개(GenericAdapter) + offers 등록, `/go/` 리다이렉터 + clicks 로그(7.3), offer 선택 엔진(7.6)
- 기본 템플릿 + JSON-LD 3종, robots.txt / sitemap / RSS, 정적·운영사·광고고지 페이지, 관리자(네트워크·오퍼·지역)

완료 기준
- [x] 모든 published 콤보 URL이 200을 반환한다
- [x] 등록되지 않은 임의 URL은 404를 반환한다
- [x] 트레일링 슬래시 없는 요청은 `/…/`로 301 된다
- [x] `/go/<offer_slug>`가 302를 반환하고, 요청 1건당 clicks 행이 정확히 1개 생긴다
- [x] 같은 콤보를 여러 번 요청해도 오퍼 순서가 같다(결정성)
- [x] compliance_ok=0 오퍼는 어떤 페이지에도 노출되지 않는다
- [x] 외부 광고 링크가 전부 `/go/` 경유 + `rel="sponsored nofollow noopener"`이다
- [x] JSON-LD 3종이 유효한 JSON이고 화면 내용과 일치한다 (로컬 테스트로 확인. 구글 리치결과 테스트는 공개 URL이 필요해 배포 후 확인)
- [x] robots.txt가 `/go/` `/lead/` `/postback/`을 차단하고 사이트맵 위치를 알린다
- [x] 사이트맵의 모든 URL이 200이다(scripts/check_sitemap.py)
- [x] Lighthouse SEO 95 이상, 모바일 성능 85 이상 (로컬 측정: 허브·지역·도구·사이트맵 모두 SEO 100 / 성능 100)
- [x] 관리자에서 네트워크·오퍼·지역을 등록·수정할 수 있다(로그인 필요)
- [ ] 네이버 서치어드바이저·구글 서치콘솔·다음 등록 완료 — 배포 후 수동

## Phase 2. 콘텐츠 파이프라인

구현: pipeline.py(생성·린트·고유성·팩트 플래그·승인·공개), prompts/*.md, 관리자 검수 화면, 강화된 품질 게이트, 실데이터 CSV 적재(court, facts), 작성자·편집기준 페이지.

완료 기준
- [x] 게이트 미통과 콤보는 publish 할 수 없다 — 고유성 미계산·린트 미실행도 실패로 처리
- [x] 공개된 형제 페이지 간 5-gram Jaccard 유사도가 전수 0.35 이하이다 — 지역명만 바꾼 시드 문장의 인천 군·구 페이지 22개는 이 기준에 걸려 비공개(review)로 내려갔다
- [x] 공개 콘텐츠의 린트 위반이 0건이다
- [x] 생성 → 린트 실패 시 피드백 재생성(최대 3회) → 검수 큐 → 승인 → 게이트 → 공개 흐름이 동작한다 (Claude 호출은 가짜로 대체해 검증)
- [ ] 허브 17개(버티컬 1 + topic 16)가 사람 검수 100%를 거쳐 공개된다 — 지금 공개 중인 허브는 시드 문장(model=seed-template)이다. 생성 경로는 둘 중 하나: (a) ANTHROPIC_API_KEY 설정 후 `flask jobs generate`(과금), (b) Claude Code 세션에서 `flask jobs prompt <url>` 로 프롬프트를 받아 직접 쓴 JSON을 `flask jobs submit <url> <파일>` 로 넣기(과금 없음, 같은 린트·고유성·검수 경로). (c) 클라우드 루틴: 서버 cron이 `flask jobs export-prompts` 로 content/queue/*.prompt.md 를 내보내 git push → Claude Code 루틴(/schedule)이 repo를 clone해 JSON을 써서 push → 서버 cron이 git pull 후 `flask jobs submit-dir` 로 적재(web_Agency 블로그 루틴과 같은 구조, 구독 사용량으로 돌고 API 키 불필요. GitHub repo + Claude GitHub App 설치가 전제). 어느 쪽이든 관리자 > 콘텐츠 검수에서 사람이 승인해야 공개된다
- [ ] 지역 페이지 중 게이트 통과분 공개 — 인천 시도 17개만 공개 중. 군·구 22개는 생성·검수 필요
- [ ] 실데이터 적재(관할 법원 전 시군구, 인구) — 인천지방법원 이름·홈페이지만 들어 있다. 법원 주소·전화, 타 지역 관할, 인구는 공식 출처 CSV를 관리자 > CSV 적재(court, facts)로 올려야 한다

## Phase 3. 어댑터 일반화 + 전환 수집

구현: `/postback/<network>`(verify → parse → upsert), CSV 임포트, stats_daily 롤업·노출 집계, EPC·캡 스냅샷 재랭킹, 플레이스먼트(pin/block/weight), A/B 테스트, iframe·script·phone·kakao 모드, 대시보드.

완료 기준
- [x] 테스트 포스트백을 보내면 conversions에 반영된다
- [x] 같은 포스트백을 중복 전송해도 행이 1개로 유지된다(dedup)
- [x] 검증 실패 포스트백은 403 + 로그, click_id 매칭 실패는 conversions_orphan에 저장된다
- [x] 신규 네트워크를 코드 변경 0줄(관리자 입력만)로 등록할 수 있다
- [x] 대시보드에 EPC가 표시된다
- [ ] 실제 네트워크 연동 — 네트워크·오퍼가 미정이라 더미로만 검증했다. 실제 네트워크의 포스트백 규격을 받아 networks.postback 에 입력해야 한다

구현하지 않은 것: click_id 없는 CSV 행의 근사 매칭(지금은 orphan), 리포트 API 폴링(해당 네트워크가 생기면 어댑터 서브클래스로), 광고 스크립트 CSP 허용 목록.

## Phase 4. 키워드 확장

구현: keywords.py(정규화·사전 태깅·콤보 생성기·카니벌 그룹·우선순위), 네이버 검색광고 API 클라이언트, 키워드 CSV 적재, 신규 page_type 6종 템플릿, 주간 공개 상한, 레거시 평탄 URL 301.

완료 기준
- [x] 콤보가 2,000개를 초과해 생성되고 priority_score가 산정된다 (4,412개)
- [x] 주당 공개 수가 설정한 상한을 넘지 않는다 (site_settings.weekly_publish_cap, 기본 100)
- [x] 카니벌 그룹마다 index 대표가 1개뿐임을 자동 검증이 통과한다
- [x] 평탄 URL(`/인천-법률/` 형태)이 디렉터리형으로 301 된다
- [ ] 검색량 수집 — 네이버 검색광고 API 키(NAVER_AD_API_KEY / NAVER_AD_SECRET / NAVER_AD_CUSTOMER_ID)가 없어 실제 호출은 못 했다. 검색량이 0이라 신규 콤보 4,354개는 전부 merged(부모로 301) 상태다. 검색량 없이 web_Agency처럼 템플릿 큐로 운영하려면 site_settings.require_volume=0 으로 두고 `flask jobs combos` 를 다시 돌리면 전부 공개 후보(draft)가 된다(스펙 8.4(a) 완화, 도어웨이 판정 위험은 11장 참고). 키를 넣고 `flask jobs kw-fetch` → `flask jobs combos` 를 돌리거나 키워드 CSV를 올리면 임계값 이상인 콤보가 draft로 올라온다
- [ ] 신규 page_type 페이지 공개 — 콘텐츠 생성(Phase 2)이 선행돼야 한다

구현하지 않은 것: 미분류 키워드의 Claude Haiku 자동 태깅(지금은 관리자 > 미분류 키워드 큐에 쌓임), 임베딩 클러스터링(동의어 사전 기반 그룹만).

## Phase 5. SEO 확장

구현: OG 이미지 배치, IndexNow 큐·키 파일·전송 잡, HTML 사이트맵, 앵커 텍스트 변형, 인접 지역 링크(인천 10쌍 시드), 도구 2개(변제금 계산기, 자가진단).

완료 기준
- [x] 도구 페이지 2개가 공개된다
- [x] OG 이미지가 공개 콤보마다 생성되고 og:image로 출력된다 (34개)
- [x] IndexNow 전송 시 큐에 sent_at·response_code가 남는다 (외부 호출은 가짜로 대체해 검증. 로컬 주소로는 전송을 거부한다)
- [ ] IndexNow 실제 전송 로그 — 배포 후 sites.indexnow_key 설정이 필요하다
- [ ] 모바일 LCP p75 실측 2.5초 이하 — 실사용자 데이터가 있어야 한다. 로컬 Lighthouse LCP는 1.0~1.1초
- [ ] 공공데이터 페이지 1개 — 넣을 통계 자료(법원 통계월보 등)를 확인하지 못해 만들지 않았다

## Phase 6. 측정 루프

구현: GSC·네이버 서치어드바이저·순위 CSV 적재, nginx 로그 파싱, 제목 테스트(순차), 정리 후보 큐, 주간 리포트.

완료 기준
- [x] 주간 리포트가 자동 생성된다 (`flask jobs report` → reports/weekly-YYYY-MM-DD.md)
- [x] 정리 후보 → 사람 승인 → 부모로 301 흐름이 동작한다
- [ ] GSC API 직접 수집 — 구글 인증 정보가 없어 CSV 업로드 방식으로만 만들었다
- [ ] cron 등록 — 서버 배포 후(아래 배치 잡 목록 참고)

## Phase 7. 버티컬 2~4

구현: 이사청소·인터넷·장기렌트 버티컬 행과 topic 용어, 버티컬별 금지 표현 린트, 버티컬별 RSS, 오퍼 없이는 활성화할 수 없는 관리자 검증. 콤보 생성기·파이프라인·템플릿은 버티컬 공용이다.

완료 기준
- [x] 각 버티컬은 오퍼가 1개 이상 확보된 뒤에만 공개된다 (세 버티컬 모두 is_active=0, 활성화 시도 시 거부)
- [ ] Phase 1~6 기준 재적용 — 오퍼·콘텐츠가 없어 공개된 페이지가 없다
- [ ] 차종 등 entities 데이터, 견적 계산기·요금표 도구 — 가격·차종 실데이터가 없어 만들지 않았다

## Phase 8. own_form (선택)

구현: `/lead/<offer_slug>` 자체 폼(동의 2종, CSRF, 요청 제한), 연락처 암호화 저장(Fernet), 전달 어댑터(webhook·sheet·email), 보유기간 후 파기 잡, 관리자 리드 목록(마스킹·열람 기록), 처리방침 문단.

완료 기준
- [x] 법무 검토 완료 전에는 OWN_FORM_ENABLED=false로 비활성 상태를 유지한다 (기본값 false, 꺼져 있으면 `/lead/`는 404이고 own_form 오퍼는 노출되지 않는다)
- [x] 동의 2종 체크 없이는 리드가 저장되지 않는다
- [x] 연락처가 암호화 저장되고, 보유기간이 지난 리드는 파기 배치로 삭제된다
- [ ] 법무 검토, 동의문 확정(site_settings: consent_collect_text, consent_third_party_text, consent_version) — 동의문이 없으면 플래그를 켜도 폼이 나오지 않는다

## 배치 잡 (cron 등록 대상)

| 잡 | 주기 | 내용 |
|---|---|---|
| `flask jobs rollup` | 매시 | clicks·conversions → stats_daily |
| `flask jobs rerank` | 매일(캡을 쓰면 매시) | EPC·캡 스냅샷, ranking_version 증가 |
| `flask jobs publish` | 매일 | 승인된 콤보를 게이트·주간 상한 안에서 공개 |
| `flask jobs quality` | 매일 | 재채점, 게이트에 걸리는 공개 콤보를 review로 |
| `flask jobs og-images` / `anchors` | 매일 | 새로 공개된 콤보의 OG 이미지·앵커 변형 |
| `flask jobs indexnow` | 매일 | IndexNow 큐 전송 |
| `flask jobs logs <파일>` | 매일 | nginx 로그 → 봇 크롤 통계 |
| `flask jobs prune` / `report` | 매주 | 정리 후보, 주간 리포트 |
| `flask jobs purge-leads` | 매일 | 보유기간 지난 리드 파기(own_form 사용 시) |
| `flask jobs generate` / `prompt`+`submit` / `kw-fetch` / `combos` / `import` | 수동 | 콘텐츠 생성(API 과금) 또는 직접 쓴 결과 제출, 키워드 수집, 콤보 재계산, CSV 적재 |
