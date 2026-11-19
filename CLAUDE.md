# 프로젝트 규칙
- 스택: Python 3.11, Flask 3, Jinja2, MariaDB(PyMySQL), gunicorn+nginx, 시스템 cron + `flask jobs`. ORM 없이 db/*.py SQL 함수. Redis/Celery 없음.
- 모든 공개 랜딩 페이지는 keyword_combos 1행이다. URL은 combos.url_path만 신뢰하고, 라우트에서 즉석 생성하지 않는다. 미등록 URL은 404, merged/retired는 parent로 301.
- URL: 디렉터리형 트레일링 슬래시 통일, 소문자, 중복 하이픈 금지, canonical은 퍼센트 인코딩 절대 URL, 쿼리 제거.
- 콘텐츠는 page_contents 승인 버전만 렌더한다. 런타임 LLM 호출·랜덤 문장 조립 금지. 오퍼 순서만 sha256(site|combo|slot|ranking_version) 시드로 결정.
- 품질 게이트(uniqueness ≤0.35, lint_ok, 실데이터 블록 ≥2, compliance_ok 오퍼 ≥1, 내부 링크 ≥5) 미통과 콤보는 publish 불가. 코드로 우회 금지.
- CPA: 페이지는 offers만 참조. 네트워크별 로직은 adapters/ 에만. 외부 광고 링크는 /go/ 경유 + rel="sponsored nofollow noopener" target=_blank. /go/ /lead/ /postback/ 은 robots 차단, no-store.
- 포스트백은 verify → parse → upsert(dedup: network_id+external_id) 순서. 검증 실패는 403 + 로그, 매칭 실패는 conversions_orphan.
- 컴플라이언스: verticals.compliance_schema 필수 필드 미충족 오퍼 노출 금지. 금지 표현 목록(lint/forbidden.txt)은 템플릿·프롬프트·관리자 입력 모두에 적용.
- 사이트맵 lastmod = content_updated_at(콘텐츠 해시 변경 시만). 날짜 조작 금지. 생성 후 scripts/check_sitemap.py 전수 200 검증.
- JSON-LD는 partials/jsonld/* 만 사용, 화면에 없는 내용 금지. 테스트에서 스키마 유효성 검사.
- 이미지 WebP(+width/height, lazy). OG 이미지는 배치 생성 /static/og/{combo_id}.webp.
- 참조 사이트 문장·FAQ·이미지 복제 금지. 구조만 참고.
- own_form 관련 코드(lead_bp, leads 테이블 암호화·파기)는 feature flag OWN_FORM_ENABLED=false 기본. 동의문 없는 폼 렌더 금지.
- 변경 시 tests/(라우팅·404·301, 결정성, 게이트, 포스트백 검증, 사이트맵, JSON-LD) 통과 후 커밋.

# 문서
- 기획서(스펙 원문): docs/spec.md — DDL은 10.1, 라우트는 10.2, 조립 알고리즘은 10.3, 오퍼 선택은 7.6, `/go/`는 7.3이 기준이다. 다르게 구현해야 하면 먼저 이유를 말하고 사용자에게 묻는다.
- 단계별 계획·완료 기준: docs/phases.md — 지시받은 Phase만 구현하고 다른 단계 기능은 만들지 않는다.

# 코드 구조
- app.py(팩토리) · web.py(공개·/go/·/postback/·/lead/·SEO) · admin.py · engine.py(오퍼 선택·조립·게이트) · pipeline.py(콘텐츠 생성·채점·공개) · keywords.py(콤보 생성기) · conv.py(전환) · jobs.py(배치 CLI) · adapters/ · db/queries.py, db/ops.py(SQL 함수) · seed.py.
- 로컬은 Python 3.12 venv. 렌더 HTML 은 DB 에 저장하지 않는다(대량 페이지에서 DB 가 HTML 로 불어나지 않게, 2026-11-18). 템플릿·코드를 고치면 서버 재시작만 하면 된다. pages 테이블은 콘텐츠 해시·OG 경로만 갖는다.
- 테스트는 시드된 DB에 대해 돈다: `python seed.py --reset` → `flask --app app jobs combos` → `python tests/test_phase1.py` → `python tests/test_phases.py`.

# 결정 사항
- 스택: Flask + Jinja2 SSR + MariaDB, Cafe24 가상서버 배포.
- 첫 업종(버티컬): 개인회생. 구조는 디렉터리형(스펙 6.3 권장)으로 구현.
- 임시값(사용자 결정 2026-10-05, 실제 값이 정해지면 교체): 사이트명, 도메인, 운영사 정보. 사이트 1(개발·테스트용)의 네트워크·오퍼는 더미 그대로 둔다.
- 업종별 서브도메인(사용자 결정 2026-11-16, 스펙 6.3의 디렉터리 권장을 대체): law(개인회생) · dui(음주운전) · baby(태아보험) · car(장기렌트·중고차) · demol(철거) · life(이사·입주청소·배관). 서브도메인 1개 = sites 1행, 업종 = verticals 1행. 요청 호스트의 첫 라벨로 사이트를 고른다(로컬: `http://law.localhost:5000/`).
- 서브도메인 사이트의 오퍼는 리플알바 실제 캠페인 코드(`seed_sites.py`). 상호·코드·고지문·연결 방식은 offers 행에만 두고 본문·제목·URL·OG에는 업체명을 넣지 않는다(업체 교체 대비).
- 업종별 금지 표현·화면 문구는 `verticals.compliance_schema`의 `badges_forbidden`·`ui`에 둔다. 머천트 규칙이 바뀌면 여기를 고친다.
- 자체 입력폼은 쓰지 않는다. 연결은 리플알바 기본 랜딩(redirect) 또는 간편폼(iframe: 본문 삽입 `ui.form=inline`, 팝업 `modal`, 버튼 `button`)뿐이다.
- 시드 순서: `python seed.py --reset` → `python seed_sites.py`(사이트 2~7, 전국 읍면동 지역 추가). 테스트는 사이트 1 기준으로 그대로 돈다.
- 공개 운영: 시드는 게이트 통과분을 전부 공개한다(로컬 확인용). 운영 서버는 `jobs stage`(허브 외 비공개) → `jobs plan <오픈일> --days 5 --then 500` → cron 의 `jobs publish`(날짜별 하루 수) 로 연다. 열린 페이지는 닫지 않는다.
- 배치 잡은 전 사이트 대상(`--site law` 로 한 곳만). 사이트맵 파일은 `jobs export-sitemaps export` 가 `export/<라벨>/` 에 쓰고 nginx 가 직접 내준다(SEO 대시보드가 같은 파일을 읽는다). OG 이미지는 서브도메인 사이트에서 업종 주제별(`jobs og-topics`).
- 배포 절차·블록: `deploy/DEPLOY.md`(211 서버, 서브도메인 nginx 는 `deploy/nginx_sites.sh`, cron 은 `deploy/cron.txt`).
- 지역(롱테일) 페이지는 업종 공통 블록의 부분집합만 쓴다(2026-11-19, raws_prj 방식): 해설 1꼭지·확인표 3행·질문 3개·유의사항 2개·출처 1개·카드 3장을 combo.id 시드로 고르고, 절차·흐름 그림은 허브에만 둔다. 첫 문장은 8벌 중 시드로. 같은 문장 비율은 `scripts/snapshot_sites.py` → site-launch `check_all.py` 로 잰다(기준 ≤30%, 현재 16~29%).
