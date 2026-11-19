-- 임시 사이트 이름 → 실제 이름(2026-11-19). 페이지 제목(page_contents.title)·설정값에 박힌 이름까지 바꾼다. 다시 실행해도 된다.
UPDATE sites SET name='상담노트 개인회생' WHERE id=2;
UPDATE sites SET name='상담노트 음주운전' WHERE id=3;
UPDATE sites SET name='상담노트 태아보험' WHERE id=4;
UPDATE sites SET name='상담노트 차량' WHERE id=5;
UPDATE sites SET name='상담노트 철거' WHERE id=6;
UPDATE sites SET name='상담노트 생활서비스' WHERE id=7;
UPDATE page_contents p JOIN keyword_combos k ON k.id=p.combo_id SET p.title=REPLACE(p.title,'회생안내(임시)','상담노트 개인회생') WHERE k.site_id=2;
UPDATE page_contents p JOIN keyword_combos k ON k.id=p.combo_id SET p.title=REPLACE(p.title,'음주사건안내(임시)','상담노트 음주운전') WHERE k.site_id=3;
UPDATE page_contents p JOIN keyword_combos k ON k.id=p.combo_id SET p.title=REPLACE(p.title,'태아보험안내(임시)','상담노트 태아보험') WHERE k.site_id=4;
UPDATE page_contents p JOIN keyword_combos k ON k.id=p.combo_id SET p.title=REPLACE(p.title,'차량안내(임시)','상담노트 차량') WHERE k.site_id=5;
UPDATE page_contents p JOIN keyword_combos k ON k.id=p.combo_id SET p.title=REPLACE(p.title,'철거안내(임시)','상담노트 철거') WHERE k.site_id=6;
UPDATE page_contents p JOIN keyword_combos k ON k.id=p.combo_id SET p.title=REPLACE(p.title,'생활서비스안내(임시)','상담노트 생활서비스') WHERE k.site_id=7;
UPDATE site_settings SET value=REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(value,'회생안내(임시)','상담노트 개인회생'),'음주사건안내(임시)','상담노트 음주운전'),'태아보험안내(임시)','상담노트 태아보험'),'차량안내(임시)','상담노트 차량'),'철거안내(임시)','상담노트 철거'),'생활서비스안내(임시)','상담노트 생활서비스') WHERE site_id>1;
SELECT s.name, (SELECT COUNT(*) FROM page_contents p JOIN keyword_combos k ON k.id=p.combo_id WHERE k.site_id=s.id AND p.title LIKE '%임시%') temp_titles FROM sites s WHERE id>1;
