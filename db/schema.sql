CREATE DATABASE IF NOT EXISTS leadsite DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE leadsite;

-- 사이트·설정 --------------------------------------------------------------
CREATE TABLE sites (
  id TINYINT UNSIGNED PRIMARY KEY, host VARCHAR(100) NOT NULL UNIQUE, name VARCHAR(60) NOT NULL,
  title_sep VARCHAR(5) NOT NULL DEFAULT ' | ', trailing_slash TINYINT(1) NOT NULL DEFAULT 1,
  naver_verification VARCHAR(64), daum_verification VARCHAR(64), google_verification VARCHAR(64), indexnow_key VARCHAR(64),
  cache_version INT UNSIGNED NOT NULL DEFAULT 1, ranking_version INT UNSIGNED NOT NULL DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE site_settings (site_id TINYINT UNSIGNED, `key` VARCHAR(60), `value` TEXT, PRIMARY KEY(site_id,`key`));
CREATE TABLE redirects (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, site_id TINYINT UNSIGNED NOT NULL,
  from_path VARCHAR(255) NOT NULL, to_path VARCHAR(255) NOT NULL, code SMALLINT DEFAULT 301, UNIQUE KEY uq(site_id,from_path));

-- 지역·실데이터 --------------------------------------------------------------
CREATE TABLE regions (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, parent_id INT UNSIGNED NULL, level TINYINT NOT NULL,  -- 1 시도 2 시군구 3 일반구 4 읍면동
  code VARCHAR(10) NOT NULL UNIQUE, slug VARCHAR(40) NOT NULL, short_name VARCHAR(40) NOT NULL, full_name VARCHAR(60) NOT NULL,
  path_slug VARCHAR(160) NOT NULL UNIQUE, path_name VARCHAR(200) NOT NULL,
  court_name VARCHAR(60), court_branch VARCHAR(60), court_address VARCHAR(200), court_phone VARCHAR(30), court_url VARCHAR(255),
  lat DECIMAL(9,6), lng DECIMAL(9,6), publish_stage TINYINT NOT NULL DEFAULT 0, sort_order INT DEFAULT 0,
  KEY idx_parent(parent_id), KEY idx_level(level), KEY idx_stage(publish_stage));
CREATE TABLE region_aliases (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, region_id INT UNSIGNED NOT NULL,
  alias_type ENUM('court','living_area','station','old_name') NOT NULL, name VARCHAR(60) NOT NULL, slug VARCHAR(60) NOT NULL,
  UNIQUE KEY uq(alias_type,slug), KEY idx_region(region_id));
CREATE TABLE region_adjacency (region_id INT UNSIGNED, adjacent_id INT UNSIGNED, PRIMARY KEY(region_id,adjacent_id));
CREATE TABLE region_facts (region_id INT UNSIGNED NOT NULL, `key` VARCHAR(40) NOT NULL, `value` VARCHAR(200), source VARCHAR(255), as_of DATE,
  PRIMARY KEY(region_id,`key`));
CREATE TABLE region_programs (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, region_id INT UNSIGNED NOT NULL, vertical_id SMALLINT UNSIGNED NULL,
  name VARCHAR(100) NOT NULL, kind VARCHAR(40), address VARCHAR(200), phone VARCHAR(30), url VARCHAR(255), note TEXT, as_of DATE, KEY idx(region_id));

-- 버티컬·주제·실데이터 -------------------------------------------------------
CREATE TABLE verticals (id SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, site_id TINYINT UNSIGNED NOT NULL,
  slug VARCHAR(30) NOT NULL, name VARCHAR(40) NOT NULL, short_label VARCHAR(30), url_base VARCHAR(40) NOT NULL,  -- '/개인회생'
  compliance_schema JSON, trust_bullets JSON, cautions JSON, procedure_tabs JSON, personas JSON, issues JSON, keyword_template TEXT,
  fallback_offer_id INT UNSIGNED NULL, form_offer_id INT UNSIGNED NULL, is_active TINYINT(1) DEFAULT 1, UNIQUE KEY uq(site_id,slug));
CREATE TABLE topic_facts (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, vertical_id SMALLINT UNSIGNED NOT NULL, topic_term_id INT UNSIGNED NULL,
  fact_key VARCHAR(60) NOT NULL, fact_text TEXT NOT NULL, source_url VARCHAR(255), verified_at DATE, KEY idx(vertical_id,topic_term_id));
CREATE TABLE price_ranges (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, vertical_id SMALLINT UNSIGNED NOT NULL, region_id INT UNSIGNED NULL,
  item VARCHAR(80) NOT NULL, min_amount INT, max_amount INT, unit VARCHAR(20), source VARCHAR(255), as_of DATE, KEY idx(vertical_id,region_id));
CREATE TABLE entities (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, vertical_id SMALLINT UNSIGNED NOT NULL, slug VARCHAR(80) NOT NULL,
  name VARCHAR(80) NOT NULL, attrs JSON, region_id INT UNSIGNED NULL, is_active TINYINT(1) DEFAULT 1, UNIQUE KEY uq(vertical_id,slug,region_id));

-- 키워드 ----------------------------------------------------------------------
CREATE TABLE keyword_dimensions (id TINYINT UNSIGNED PRIMARY KEY, slug VARCHAR(20) NOT NULL UNIQUE,  -- topic,intent,situation,persona,region,question,entity
  name VARCHAR(30) NOT NULL, rank_order TINYINT NOT NULL);
CREATE TABLE keyword_terms (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, vertical_id SMALLINT UNSIGNED NULL, dimension_id TINYINT UNSIGNED NOT NULL,
  term VARCHAR(80) NOT NULL, slug VARCHAR(80) NOT NULL, canonical_term_id INT UNSIGNED NULL,   -- 동의어는 대표 term 지정
  region_id INT UNSIGNED NULL, entity_id INT UNSIGNED NULL,
  volume_pc INT, volume_mobile INT, competition DECIMAL(4,3), metrics JSON, metrics_updated_at DATE,
  is_active TINYINT(1) DEFAULT 1, UNIQUE KEY uq(vertical_id,dimension_id,slug), KEY idx_dim(dimension_id), KEY idx_canon(canonical_term_id));
CREATE TABLE keyword_synonyms (term_id INT UNSIGNED, variant VARCHAR(80), source VARCHAR(30), PRIMARY KEY(term_id,variant));
CREATE TABLE keyword_combos (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, site_id TINYINT UNSIGNED NOT NULL, vertical_id SMALLINT UNSIGNED NOT NULL,
  page_type VARCHAR(30) NOT NULL,            -- vertical_hub, topic_hub, topic_intent, topic_situation, topic_persona, situation_persona,
                                             -- region_topic, region_topic_intent, court_alias, entity, region_entity, question, tool
  topic_id INT UNSIGNED NULL, intent_id INT UNSIGNED NULL, situation_id INT UNSIGNED NULL, persona_id INT UNSIGNED NULL,
  region_id INT UNSIGNED NULL, alias_id INT UNSIGNED NULL, entity_id INT UNSIGNED NULL, question_id INT UNSIGNED NULL,
  primary_keyword VARCHAR(120) NOT NULL, url_path VARCHAR(255) NULL, parent_combo_id INT UNSIGNED NULL,
  cannibal_group_id INT UNSIGNED NULL, is_group_rep TINYINT(1) DEFAULT 1,
  volume_total INT DEFAULT 0, competition DECIMAL(4,3), priority_score DECIMAL(8,3) DEFAULT 0, quality_score DECIMAL(5,2) NULL,
  status ENUM('draft','generated','review','approved','published','noindex','merged','retired') NOT NULL DEFAULT 'draft',
  content_version INT UNSIGNED NULL, published_at DATETIME NULL, content_updated_at DATETIME NULL, retired_at DATETIME NULL,
  UNIQUE KEY uq_dims(site_id,vertical_id,page_type,topic_id,intent_id,situation_id,persona_id,region_id,alias_id,entity_id,question_id),
  UNIQUE KEY uq_url(site_id,url_path), KEY idx_status(site_id,status), KEY idx_parent(parent_combo_id), KEY idx_region(region_id,page_type), KEY idx_group(cannibal_group_id), KEY idx_prio(priority_score));

-- 콘텐츠 ----------------------------------------------------------------------
CREATE TABLE page_contents (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, combo_id INT UNSIGNED NOT NULL, version INT UNSIGNED NOT NULL,
  h1 VARCHAR(150), title VARCHAR(160), meta_description VARCHAR(200), meta_keywords TEXT, blocks JSON NOT NULL, faqs JSON, links JSON,
  model VARCHAR(40), prompt_version VARCHAR(20), uniqueness_score DECIMAL(4,3), lint_result JSON, fact_flags JSON,
  status ENUM('generated','lint_fail','lint_ok','review','approved','rejected') DEFAULT 'generated',
  reviewer VARCHAR(40), reviewed_at DATETIME, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, UNIQUE KEY uq(combo_id,version));
CREATE TABLE pages (                                   -- 렌더 캐시
  combo_id INT UNSIGNED PRIMARY KEY, rendered_html MEDIUMTEXT, rendered_at DATETIME, cache_version INT UNSIGNED, ranking_version INT UNSIGNED,
  content_hash CHAR(32), og_image_path VARCHAR(255), in_sitemap TINYINT(1) DEFAULT 0, noindex TINYINT(1) DEFAULT 0);
CREATE TABLE posts (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, site_id TINYINT UNSIGNED NOT NULL, vertical_id SMALLINT UNSIGNED NULL,
  combo_id INT UNSIGNED NULL, slug VARCHAR(120) NOT NULL, title VARCHAR(150) NOT NULL, description VARCHAR(200), body_html MEDIUMTEXT NOT NULL,
  thumbnail_path VARCHAR(255), tags JSON, author_id INT UNSIGNED, reviewer_id INT UNSIGNED NULL, sources JSON,
  status ENUM('draft','scheduled','published') DEFAULT 'draft', published_at DATETIME, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq(site_id,slug), KEY idx_pub(site_id,status,published_at));
CREATE TABLE authors (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, slug VARCHAR(40) NOT NULL UNIQUE, name VARCHAR(40) NOT NULL, role VARCHAR(60), bio TEXT, photo_path VARCHAR(255), links JSON);
CREATE TABLE anchor_variants (combo_id INT UNSIGNED NOT NULL, anchor VARCHAR(120) NOT NULL, PRIMARY KEY(combo_id,anchor));
CREATE TABLE keyword_image_pool (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, vertical_id SMALLINT UNSIGNED NOT NULL, topic_id INT UNSIGNED NULL,
  keyword VARCHAR(60) NOT NULL, image_path VARCHAR(255) NOT NULL, width SMALLINT, height SMALLINT, is_active TINYINT(1) DEFAULT 1);
CREATE TABLE faq_pool (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, vertical_id SMALLINT UNSIGNED NULL, topic_id INT UNSIGNED NULL,
  scope ENUM('vertical','topic','site') NOT NULL DEFAULT 'topic', question VARCHAR(200) NOT NULL, answer TEXT NOT NULL, source_url VARCHAR(255), is_active TINYINT(1) DEFAULT 1);
CREATE TABLE network_sites (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, name VARCHAR(60), url VARCHAR(255), vertical_tag VARCHAR(30), rel VARCHAR(30) DEFAULT 'nofollow', is_active TINYINT(1) DEFAULT 1);

-- CPA 연동 --------------------------------------------------------------------
CREATE TABLE networks (
  id SMALLINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, slug VARCHAR(30) NOT NULL UNIQUE, name VARCHAR(60) NOT NULL,
  kind ENUM('cpa_network','affiliate_cps','direct_advertiser','call_tracking') NOT NULL,
  adapter_class VARCHAR(80) NULL,                       -- NULL = GenericAdapter
  conventions JSON,                                     -- {click_param:'sub1', extra_params:{sub2:'{region_slug}'}, max_sub_len:64}
  postback JSON,                                        -- {enabled, method, click_id_param, status_param, amount_param, event_param, secret_param, secret, ip_allowlist[], status_map{}}
  report_api JSON, csv_mapping JSON, currency CHAR(3) DEFAULT 'KRW', settlement_note VARCHAR(200), is_active TINYINT(1) DEFAULT 1);
CREATE TABLE offers (
  id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, network_id SMALLINT UNSIGNED NOT NULL, vertical_id SMALLINT UNSIGNED NOT NULL,
  slug VARCHAR(60) NOT NULL UNIQUE, advertiser VARCHAR(100) NOT NULL, name VARCHAR(100) NOT NULL, category VARCHAR(40),
  payout_type ENUM('CPA','CPL','CPS','CPC','CALL') NOT NULL, payout_amount DECIMAL(12,2), payout_note VARCHAR(200),
  integration_mode ENUM('redirect','iframe','script','own_form','phone','kakao') NOT NULL,
  tracking_url_template VARCHAR(500), embed_url_template VARCHAR(500), script_snippet TEXT, phone VARCHAR(30), kakao_url VARCHAR(255),
  forward_config JSON,                                  -- own_form: {method:'webhook'|'api'|'email'|'sheet', url, headers, field_map, consent_version}
  region_scope JSON, topic_scope JSON, start_at DATETIME, end_at DATETIME,
  daily_cap INT, monthly_cap INT, manual_weight TINYINT DEFAULT 1,
  compliance_fields JSON,                               -- {ad_lawyer:'이재열', biz_no:'...'} verticals.compliance_schema 검증 대상
  compliance_ok TINYINT(1) DEFAULT 0, logo_path VARCHAR(255), blurb TEXT, badge VARCHAR(30), creatives JSON,
  status ENUM('draft','active','paused','ended') DEFAULT 'draft', KEY idx_v(vertical_id,status), KEY idx_n(network_id));
CREATE TABLE offer_placements (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, offer_id INT UNSIGNED NOT NULL,
  scope_type ENUM('site','vertical','page_type','combo') NOT NULL, scope_id INT UNSIGNED, slot VARCHAR(30) NOT NULL,
  mode ENUM('pin','block','weight') NOT NULL, weight TINYINT, position TINYINT, KEY idx(scope_type,scope_id,slot));
CREATE TABLE ab_tests (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, name VARCHAR(60), scope_type VARCHAR(20), scope_id INT UNSIGNED, slot VARCHAR(30),
  arms JSON, start_at DATETIME, end_at DATETIME, status ENUM('draft','running','done') DEFAULT 'draft', winner VARCHAR(20));
CREATE TABLE clicks (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, click_id CHAR(22) NOT NULL UNIQUE, offer_id INT UNSIGNED NOT NULL, combo_id INT UNSIGNED NULL,
  region_id INT UNSIGNED NULL, keyword_term_id INT UNSIGNED NULL, slot VARCHAR(30), variant VARCHAR(20), ua_hash CHAR(32), ip_hash CHAR(32),
  referrer VARCHAR(255), is_bot TINYINT(1) DEFAULT 0, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, KEY idx_offer_dt(offer_id,created_at), KEY idx_combo(combo_id));
CREATE TABLE leads (                                   -- own_form 전용
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, offer_id INT UNSIGNED NOT NULL, click_id CHAR(22) NULL, combo_id INT UNSIGNED NULL,
  name_enc VARBINARY(255), phone_enc VARBINARY(255), region_id INT UNSIGNED, fields JSON, consent_collect TINYINT(1) NOT NULL, consent_third_party TINYINT(1) NOT NULL,
  consent_version VARCHAR(20), consent_text_hash CHAR(32), ip_hash CHAR(32), forwarded_at DATETIME, forward_result JSON, external_lead_id VARCHAR(80),
  retention_until DATE NOT NULL, purged_at DATETIME NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, KEY idx_offer(offer_id,created_at));
CREATE TABLE conversions (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, network_id SMALLINT UNSIGNED NOT NULL, offer_id INT UNSIGNED NULL, click_id CHAR(22) NULL, lead_id BIGINT UNSIGNED NULL,
  external_id VARCHAR(80), event VARCHAR(30) DEFAULT 'lead', status ENUM('pending','approved','rejected') DEFAULT 'pending', amount DECIMAL(12,2),
  source ENUM('postback','api','csv','manual') NOT NULL, match_quality ENUM('exact','estimated') DEFAULT 'exact', raw JSON,
  occurred_at DATETIME, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME,
  UNIQUE KEY uq_ext(network_id,external_id), KEY idx_click(click_id), KEY idx_offer(offer_id,occurred_at));
CREATE TABLE conversions_orphan (id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, network_id SMALLINT UNSIGNED, raw JSON, reason VARCHAR(100), created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE stats_daily (`date` DATE, site_id TINYINT UNSIGNED, combo_id INT UNSIGNED, offer_id INT UNSIGNED, region_id INT UNSIGNED, slot VARCHAR(30), variant VARCHAR(20),
  impressions INT DEFAULT 0, clicks INT DEFAULT 0, leads INT DEFAULT 0, conv_pending INT DEFAULT 0, conv_approved INT DEFAULT 0, revenue DECIMAL(14,2) DEFAULT 0,
  PRIMARY KEY(`date`,site_id,combo_id,offer_id,slot,variant));

-- 측정·운영 --------------------------------------------------------------------
CREATE TABLE gsc_daily (`date` DATE, site_id TINYINT UNSIGNED, page VARCHAR(255), query VARCHAR(200), device VARCHAR(10), clicks INT, impressions INT, ctr DECIMAL(6,4), position DECIMAL(6,2), PRIMARY KEY(`date`,site_id,page,query,device));
CREATE TABLE naver_sa_weekly (week DATE, site_id TINYINT UNSIGNED, metric VARCHAR(40), dim VARCHAR(255), `value` INT, PRIMARY KEY(week,site_id,metric,dim));
CREATE TABLE rank_daily (`date` DATE, combo_id INT UNSIGNED, engine ENUM('naver','google'), keyword VARCHAR(120), position SMALLINT, url VARCHAR(255), PRIMARY KEY(`date`,combo_id,engine,keyword));
CREATE TABLE bot_crawl_daily (`date` DATE, path VARCHAR(255), bot VARCHAR(20), hits INT, status SMALLINT, PRIMARY KEY(`date`,path,bot,status));
CREATE TABLE title_tests (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, combo_id INT UNSIGNED, variant_title VARCHAR(160), variant_desc VARCHAR(200), start_at DATE, end_at DATE, impressions INT, clicks INT, status VARCHAR(20));
CREATE TABLE indexnow_queue (id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY, site_id TINYINT UNSIGNED, url VARCHAR(1000), reason VARCHAR(20), queued_at DATETIME DEFAULT CURRENT_TIMESTAMP, sent_at DATETIME, response_code SMALLINT);
CREATE TABLE job_queue (id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, job VARCHAR(40) NOT NULL, payload JSON, status ENUM('queued','running','done','failed') DEFAULT 'queued', attempts TINYINT DEFAULT 0, run_after DATETIME, log TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, KEY idx(status,run_after));
CREATE TABLE audit_log (id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY, actor VARCHAR(40), action VARCHAR(60), target VARCHAR(80), diff JSON, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
