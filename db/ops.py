"""Phase 2~8 SQL 함수: 링크·플레이스먼트, 전환, 통계, 콘텐츠 파이프라인, 키워드, 측정, 리드."""
import json

from . import ex, q, q1

J = lambda v: json.dumps(v, ensure_ascii=False, default=str)  # noqa: E731


def set_setting(site_id, key, value):
    ex("REPLACE INTO site_settings (site_id, `key`, `value`) VALUES (%s,%s,%s)", (site_id, key, value))


# ── 렌더 보조 ─────────────────────────────────────────────────────────────
def placements(slot, vertical_id, combo_id):
    return q("""SELECT offer_id, mode, weight, position FROM offer_placements WHERE slot=%s AND
                (scope_type='site' OR (scope_type='vertical' AND scope_id=%s) OR (scope_type='combo' AND scope_id=%s)) ORDER BY id""",
             (slot, vertical_id, combo_id))


def adjacent_combos(c, n=6):
    """인접 지역의 같은 유형·같은 topic 페이지."""
    return q("""SELECT k.id, k.url_path, k.primary_keyword FROM region_adjacency a JOIN keyword_combos k ON k.region_id=a.adjacent_id
                WHERE a.region_id=%s AND k.vertical_id=%s AND k.status='published' AND k.page_type=%s AND k.topic_id<=>%s AND k.intent_id<=>%s
                ORDER BY k.id LIMIT %s""", (c["region_id"], c["vertical_id"], c["page_type"], c["topic_id"], c["intent_id"], n))


def anchor_variants(combo_ids):
    out = {}
    if combo_ids:
        for r in q("SELECT combo_id, anchor FROM anchor_variants WHERE combo_id IN (%s) ORDER BY combo_id, anchor"
                   % ",".join(["%s"] * len(combo_ids)), combo_ids):
            out.setdefault(r["combo_id"], []).append(r["anchor"])
    return out


def price_ranges(vertical_id, region_id=None):
    return q("SELECT * FROM price_ranges WHERE vertical_id=%s AND (region_id IS NULL OR region_id<=>%s) ORDER BY id", (vertical_id, region_id))


def default_author():
    return q1("SELECT * FROM authors ORDER BY id LIMIT 1")


def author(slug):
    return q1("SELECT * FROM authors WHERE slug=%s", (slug,))


def running_title_test(combo_id):
    return q1("SELECT * FROM title_tests WHERE combo_id=%s AND status='running' AND start_at<=CURDATE() AND end_at>=CURDATE() ORDER BY id LIMIT 1",
              (combo_id,))


def running_ab_tests(vertical_id, combo_id):
    return q("""SELECT * FROM ab_tests WHERE status='running' AND (start_at IS NULL OR start_at<=NOW()) AND (end_at IS NULL OR end_at>=NOW())
                AND (scope_type='site' OR (scope_type='vertical' AND scope_id=%s) OR (scope_type='combo' AND scope_id=%s)) ORDER BY id""",
             (vertical_id, combo_id))


def vertical_by_base(site_id, url_base):
    return q1("SELECT * FROM verticals WHERE site_id=%s AND url_base=%s AND is_active=1", (site_id, url_base))


def sitemap_regions(vertical_id):
    return q("""SELECT k.url_path, k.primary_keyword, r.level FROM keyword_combos k JOIN regions r ON r.id=k.region_id
                WHERE k.vertical_id=%s AND k.status='published' AND k.page_type='region_topic' ORDER BY r.sort_order, k.id""", (vertical_id,))


def latest_published_vertical(vertical_id, n=20):
    return q("""SELECT c.url_path, c.content_updated_at, p.title, p.meta_description FROM keyword_combos c
                JOIN page_contents p ON p.combo_id=c.id AND p.version=c.content_version
                WHERE c.vertical_id=%s AND c.status='published' ORDER BY c.content_updated_at DESC, c.id LIMIT %s""", (vertical_id, n))


# ── 전환(Phase 3) ─────────────────────────────────────────────────────────
def network_by_slug(slug):
    return q1("SELECT * FROM networks WHERE slug=%s AND is_active=1", (slug,))


def click(click_id):
    return q1("SELECT * FROM clicks WHERE click_id=%s", (click_id,))


def conversion(network_id, external_id):
    return q1("SELECT * FROM conversions WHERE network_id=%s AND external_id=%s", (network_id, external_id))


def insert_conversion(**c):
    return ex(f"INSERT INTO conversions ({','.join(c)}) VALUES ({','.join(['%s'] * len(c))})", list(c.values()))


def update_conversion(conv_id, status, amount):
    ex("UPDATE conversions SET status=%s, amount=COALESCE(%s, amount), updated_at=NOW() WHERE id=%s", (status, amount, conv_id))


def insert_orphan(network_id, raw, reason):
    ex("INSERT INTO conversions_orphan (network_id, raw, reason) VALUES (%s,%s,%s)", (network_id, J(raw), reason))


def add_impressions(site_id, combo_id, shown):
    """shown = [(slot, offer_id, variant)]"""
    if shown:
        ex("INSERT INTO stats_daily (`date`, site_id, combo_id, offer_id, slot, variant, impressions) VALUES "
           + ",".join(["(CURDATE(),%s,%s,%s,%s,%s,1)"] * len(shown)) + " ON DUPLICATE KEY UPDATE impressions=impressions+1",
           [v for slot, offer_id, variant in shown for v in (site_id, combo_id, offer_id, slot, variant)])


def rollup(site_id, day):
    """clicks·conversions → stats_daily. 하루치를 다시 계산해 덮어쓴다(노출 수는 실시간 누적값 유지)."""
    ex("UPDATE stats_daily SET clicks=0, leads=0, conv_pending=0, conv_approved=0, revenue=0 WHERE `date`=%s AND site_id=%s", (day, site_id))
    ex("""INSERT INTO stats_daily (`date`, site_id, combo_id, offer_id, region_id, slot, variant, clicks)
          SELECT DATE(created_at), %s, IFNULL(combo_id,0), offer_id, MAX(region_id), IFNULL(slot,''), IFNULL(variant,''), COUNT(*)
          FROM clicks WHERE is_bot=0 AND DATE(created_at)=%s GROUP BY IFNULL(combo_id,0), offer_id, IFNULL(slot,''), IFNULL(variant,'')
          ON DUPLICATE KEY UPDATE clicks=VALUES(clicks)""", (site_id, day))
    ex("""INSERT INTO stats_daily (`date`, site_id, combo_id, offer_id, region_id, slot, variant, conv_pending, conv_approved, revenue)
          SELECT %s, %s, IFNULL(k.combo_id,0), v.offer_id, MAX(k.region_id), IFNULL(k.slot,''), IFNULL(k.variant,''),
                 SUM(v.status='pending'), SUM(v.status='approved'), SUM(IF(v.status='approved', IFNULL(v.amount,0), 0))
          FROM conversions v LEFT JOIN clicks k ON k.click_id=v.click_id
          WHERE v.offer_id IS NOT NULL AND DATE(IFNULL(v.occurred_at, v.created_at))=%s
          GROUP BY IFNULL(k.combo_id,0), v.offer_id, IFNULL(k.slot,''), IFNULL(k.variant,'')
          ON DUPLICATE KEY UPDATE conv_pending=VALUES(conv_pending), conv_approved=VALUES(conv_approved), revenue=VALUES(revenue)""",
       (day, site_id, day))
    ex("""INSERT INTO stats_daily (`date`, site_id, combo_id, offer_id, slot, variant, leads)
          SELECT %s, %s, IFNULL(combo_id,0), offer_id, 'form', '', COUNT(*) FROM leads WHERE DATE(created_at)=%s GROUP BY IFNULL(combo_id,0), offer_id
          ON DUPLICATE KEY UPDATE leads=VALUES(leads)""", (day, site_id, day))


def offer_stats_7d():
    return q("""SELECT o.id, o.daily_cap, o.monthly_cap,
                  (SELECT COUNT(*) FROM clicks k WHERE k.offer_id=o.id AND k.is_bot=0 AND k.created_at>=NOW()-INTERVAL 7 DAY) clicks,
                  (SELECT IFNULL(SUM(amount),0) FROM conversions v WHERE v.offer_id=o.id AND v.status='approved' AND v.created_at>=NOW()-INTERVAL 7 DAY) revenue,
                  (SELECT COUNT(*) FROM conversions v WHERE v.offer_id=o.id AND v.created_at>=NOW()-INTERVAL 7 DAY) convs,
                  (SELECT COUNT(*) FROM conversions v WHERE v.offer_id=o.id AND v.status='rejected' AND v.created_at>=NOW()-INTERVAL 7 DAY) rejected,
                  (SELECT COUNT(*) FROM conversions v WHERE v.offer_id=o.id AND v.status<>'rejected' AND DATE(v.created_at)=CURDATE()) today,
                  (SELECT COUNT(*) FROM conversions v WHERE v.offer_id=o.id AND v.status<>'rejected' AND v.created_at>=DATE_FORMAT(NOW(),'%%Y-%%m-01')) month
                FROM offers o""")


def bump_ranking_version(site_id):
    ex("UPDATE sites SET ranking_version=ranking_version+1 WHERE id=%s", (site_id,))


def dashboard(group, days=30):
    """group: offer | combo | region | slot"""
    name, join = {
        "offer": ("o.advertiser", "JOIN offers o ON o.id=s.offer_id"),
        "combo": ("IFNULL(k.primary_keyword,'(페이지 없음)')", "LEFT JOIN keyword_combos k ON k.id=s.combo_id"),
        "region": ("IFNULL(r.path_name,'(지역 없음)')", "LEFT JOIN regions r ON r.id=s.region_id"),
        "slot": ("CONCAT(s.slot, IF(s.variant<>'', CONCAT(' / ', s.variant), ''))", ""),
    }[group]
    return q(f"""SELECT {name} name, SUM(s.impressions) impressions, SUM(s.clicks) clicks, SUM(s.leads) leads,
                   SUM(s.conv_pending) conv_pending, SUM(s.conv_approved) conv_approved, SUM(s.revenue) revenue
                 FROM stats_daily s {join} WHERE s.`date`>=CURDATE()-INTERVAL %s DAY GROUP BY name ORDER BY revenue DESC, clicks DESC LIMIT 50""", (days,))


# ── 콘텐츠 파이프라인(Phase 2) ────────────────────────────────────────────
def sibling_contents(combo):
    """고유성 비교 대상: 같은 부모·같은 page_type 콤보의 현재 버전 블록."""
    return q("""SELECT k.id combo_id, p.blocks FROM keyword_combos k JOIN page_contents p ON p.combo_id=k.id AND p.version=k.content_version
                WHERE k.parent_combo_id<=>%s AND k.page_type=%s AND k.id<>%s AND k.parent_combo_id IS NOT NULL""",
             (combo["parent_combo_id"], combo["page_type"], combo["id"]))


def latest_content(combo_id):
    return q1("SELECT * FROM page_contents WHERE combo_id=%s ORDER BY version DESC LIMIT 1", (combo_id,))


def content_by_id(content_id):
    return q1("SELECT * FROM page_contents WHERE id=%s", (content_id,))


def insert_content(combo_id, version, data, model, prompt_version):
    return ex("""INSERT INTO page_contents (combo_id, version, h1, title, meta_description, blocks, faqs, links, model, prompt_version, status)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'generated')""",
              (combo_id, version, data["h1"][:150], data["title"][:160], data["meta_description"][:200], J(data["blocks"]),
               J(data.get("faqs") or []) if data.get("faqs") else None, J(data.get("internal_link_suggestions") or []), model, prompt_version))


def save_scores(content_id, uniqueness, lint_result, fact_flags, status=None):
    ex("UPDATE page_contents SET uniqueness_score=%s, lint_result=%s, fact_flags=%s" + (", status=%s" if status else "") + " WHERE id=%s",
       [uniqueness, J(lint_result), J(fact_flags)] + ([status] if status else []) + [content_id])


def review_content(content_id, status, reviewer):
    ex("UPDATE page_contents SET status=%s, reviewer=%s, reviewed_at=NOW() WHERE id=%s", (status, reviewer, content_id))


def set_combo(combo_id, **fields):
    ex(f"UPDATE keyword_combos SET {','.join(f'{k}=%s' for k in fields)} WHERE id=%s", list(fields.values()) + [combo_id])


def review_queue():
    return q("""SELECT p.id, p.combo_id, p.version, p.status, p.h1, p.uniqueness_score, p.model, k.url_path, k.page_type
                FROM page_contents p JOIN keyword_combos k ON k.id=p.combo_id WHERE p.status IN ('review','lint_fail','lint_ok','generated')
                ORDER BY p.id LIMIT 200""")


POST_TYPE = "question"  # 질문형 글(미리 써 둔 글을 하루 몇 편씩 연다). 지역 페이지 공개 몫과 따로 센다


def combos_by_status(site_id, statuses, limit=5000, with_url=True, posts=None):
    """posts=None 전체 / True 질문 글만 / False 질문 글 제외."""
    kind = "" if posts is None else (" AND page_type='question'" if posts else " AND page_type<>'question'")
    return q("SELECT * FROM keyword_combos WHERE site_id=%s AND status IN (" + ",".join(["%s"] * len(statuses)) + ")"
             + (" AND url_path IS NOT NULL" if with_url else "") + kind + " ORDER BY priority_score DESC, id LIMIT %s", [site_id, *statuses, limit])


def published_last_days(site_id, days=7):
    return q1("SELECT COUNT(*) n FROM keyword_combos WHERE site_id=%s AND published_at>=NOW()-INTERVAL %s DAY", (site_id, days))["n"]


def published_today(site_id, posts=False):
    kind = "=" if posts else "<>"
    return q1(f"SELECT COUNT(*) n FROM keyword_combos WHERE site_id=%s AND published_at>=CURDATE() AND page_type{kind}'question'", (site_id,))["n"]


def count_posts(site_id):
    return {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM keyword_combos WHERE site_id=%s AND page_type='question' GROUP BY status", (site_id,))}


def stage_all(site_id, keep_types=()):
    """공개 페이지를 모두 approved 로 되돌린다(오픈 전 준비). keep_types 의 page_type 은 그대로 둔다."""
    keep = " AND page_type NOT IN (" + ",".join(["%s"] * len(keep_types)) + ")" if keep_types else ""
    return ex("UPDATE keyword_combos SET status='approved', published_at=NULL, content_updated_at=NULL WHERE site_id=%s AND status='published'" + keep,
              [site_id, *keep_types])


def count_status(site_id):
    return {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM keyword_combos WHERE site_id=%s GROUP BY status", (site_id,))}


def publish_combo(combo_id):
    ex("UPDATE keyword_combos SET status='published', published_at=IFNULL(published_at, NOW()), content_updated_at=IFNULL(content_updated_at, NOW()) WHERE id=%s",
       (combo_id,))


# ── 키워드(Phase 4) ───────────────────────────────────────────────────────
def terms(vertical_id):
    return q("""SELECT t.*, d.slug dim FROM keyword_terms t JOIN keyword_dimensions d ON d.id=t.dimension_id
                WHERE t.vertical_id=%s AND t.is_active=1 ORDER BY t.id""", (vertical_id,))


def synonyms(vertical_id):
    return q("SELECT s.term_id, s.variant FROM keyword_synonyms s JOIN keyword_terms t ON t.id=s.term_id WHERE t.vertical_id=%s", (vertical_id,))


def regions_upto(level):
    return q("SELECT * FROM regions WHERE level<=%s ORDER BY sort_order, id", (level,))


def aliases(alias_type):
    return q("SELECT * FROM region_aliases WHERE alias_type=%s ORDER BY id", (alias_type,))


def all_combos(vertical_id):
    return q("SELECT * FROM keyword_combos WHERE vertical_id=%s ORDER BY id", (vertical_id,))


def insert_combo(**c):
    return ex(f"INSERT INTO keyword_combos ({','.join(c)}) VALUES ({','.join(['%s'] * len(c))})", list(c.values()))


def update_term_metrics(term_id, pc, mobile, competition):
    ex("UPDATE keyword_terms SET volume_pc=%s, volume_mobile=%s, competition=%s, metrics_updated_at=CURDATE() WHERE id=%s", (pc, mobile, competition, term_id))


def enqueue(job, payload):
    return ex("INSERT INTO job_queue (job, payload) VALUES (%s,%s)", (job, J(payload)))


def queued(job):
    return q("SELECT * FROM job_queue WHERE job=%s AND status='queued' ORDER BY id LIMIT 500", (job,))


def job(job_id):
    return q1("SELECT * FROM job_queue WHERE id=%s", (job_id,))


def finish_job(job_id, status, log=None):
    ex("UPDATE job_queue SET status=%s, log=%s WHERE id=%s", (status, log, job_id))


def max_payout(vertical_id):
    return float(q1("SELECT IFNULL(MAX(payout_amount),0) m FROM offers WHERE vertical_id=%s AND status='active'", (vertical_id,))["m"])


def group_rep_violations(vertical_id):
    """카니벌 그룹마다 대표(is_group_rep=1)는 정확히 1개여야 한다."""
    return q("""SELECT cannibal_group_id, SUM(is_group_rep) reps FROM keyword_combos WHERE vertical_id=%s AND cannibal_group_id IS NOT NULL
                GROUP BY cannibal_group_id HAVING reps<>1""", (vertical_id,))


# ── SEO 확장(Phase 5) ─────────────────────────────────────────────────────
def set_topic_og(topic_id, path):
    ex("""INSERT INTO pages (combo_id, og_image_path) SELECT id, %s FROM keyword_combos WHERE topic_id=%s
          ON DUPLICATE KEY UPDATE og_image_path=VALUES(og_image_path)""", (path, topic_id))


def set_og_image(combo_id, path):
    ex("INSERT INTO pages (combo_id, og_image_path) VALUES (%s,%s) ON DUPLICATE KEY UPDATE og_image_path=VALUES(og_image_path)", (combo_id, path))


def indexnow_pending(site_id, limit):
    return q("SELECT * FROM indexnow_queue WHERE site_id=%s AND sent_at IS NULL ORDER BY id LIMIT %s", (site_id, limit))


def indexnow_mark(ids, code):
    """성공(200·202)만 보낸 것으로 표시한다. 거절(403 등)은 응답 코드만 남기고 다음 실행 때 다시 보낸다."""
    sent = "NOW()" if code in (200, 202) else "NULL"
    ex(f"UPDATE indexnow_queue SET sent_at={sent}, response_code=%s WHERE id IN (" + ",".join(["%s"] * len(ids)) + ")", [code, *ids])


def replace_anchors(combo_id, anchors):
    ex("DELETE FROM anchor_variants WHERE combo_id=%s", (combo_id,))
    for a in anchors:
        ex("INSERT IGNORE INTO anchor_variants (combo_id, anchor) VALUES (%s,%s)", (combo_id, a))


# ── 측정(Phase 6) ─────────────────────────────────────────────────────────
IMPORTS = {  # kind → (table, CSV 열 순서)
    "gsc": ("gsc_daily", ["date", "page", "query", "device", "clicks", "impressions", "ctr", "position"]),
    "naver": ("naver_sa_weekly", ["week", "metric", "dim", "value"]),
    "rank": ("rank_daily", ["date", "combo_id", "engine", "keyword", "position", "url"]),
}


def import_rows(kind, site_id, rows):
    table, cols = IMPORTS[kind]
    with_site = kind != "rank"
    names = cols[:1] + (["site_id"] if with_site else []) + cols[1:]
    sql = (f"REPLACE INTO {table} ({','.join('`%s`' % c for c in names)}) VALUES ({','.join(['%s'] * len(names))})")
    for r in rows:
        ex(sql, [r[cols[0]]] + ([site_id] if with_site else []) + [r.get(c) or None for c in cols[1:]])
    return len(rows)


def add_bot_hits(rows):
    for (day, path, bot, status), hits in rows.items():
        ex("REPLACE INTO bot_crawl_daily (`date`, path, bot, hits, status) VALUES (%s,%s,%s,%s,%s)", (day, path, bot, hits, status))


def prune_candidates(site_id, min_days, base_url):
    return q("""SELECT k.id, k.url_path, k.primary_keyword,
                  (SELECT IFNULL(SUM(impressions),0) FROM gsc_daily g WHERE g.site_id=k.site_id AND g.page=CONCAT(%s, k.url_path)) impressions,
                  (SELECT IFNULL(SUM(clicks),0) FROM gsc_daily g WHERE g.site_id=k.site_id AND g.page=CONCAT(%s, k.url_path)) gsc_clicks,
                  (SELECT COUNT(*) FROM conversions v JOIN clicks c ON c.click_id=v.click_id WHERE c.combo_id=k.id) convs
                FROM keyword_combos k WHERE k.site_id=%s AND k.status='published' AND k.parent_combo_id IS NOT NULL
                  AND k.published_at<=NOW()-INTERVAL %s DAY
                HAVING impressions<50 OR (gsc_clicks=0 AND convs=0)""", (base_url, base_url, site_id, min_days))


def weekly_numbers(site_id):
    return {
        "published": q1("SELECT COUNT(*) n FROM keyword_combos WHERE site_id=%s AND status='published'", (site_id,))["n"],
        "published_7d": published_last_days(site_id),
        "stats": q1("""SELECT IFNULL(SUM(impressions),0) impressions, IFNULL(SUM(clicks),0) clicks, IFNULL(SUM(conv_approved),0) approved,
                         IFNULL(SUM(revenue),0) revenue FROM stats_daily WHERE site_id=%s AND `date`>=CURDATE()-INTERVAL 7 DAY""", (site_id,)),
        "queries": q("""SELECT query, SUM(clicks) clicks, SUM(impressions) impressions FROM gsc_daily WHERE site_id=%s AND `date`>=CURDATE()-INTERVAL 7 DAY
                        GROUP BY query ORDER BY clicks DESC, impressions DESC LIMIT 10""", (site_id,)),
        "bots": q("SELECT bot, SUM(hits) hits, SUM(IF(status>=400, hits, 0)) errors FROM bot_crawl_daily WHERE `date`>=CURDATE()-INTERVAL 7 DAY GROUP BY bot"),
        "title_tests": q("SELECT t.id, t.variant_title, t.status, t.impressions, t.clicks, k.url_path FROM title_tests t JOIN keyword_combos k ON k.id=t.combo_id ORDER BY t.id DESC LIMIT 10"),
        "prune_queue": q1("SELECT COUNT(*) n FROM job_queue WHERE job='prune' AND status='queued'")["n"],
        "review_queue": q1("SELECT COUNT(*) n FROM page_contents WHERE status IN ('review','lint_fail')")["n"],
    }


def title_test_metrics(base_url):
    """테스트 기간의 GSC 노출·클릭을 title_tests 에 반영."""
    ex("""UPDATE title_tests t JOIN keyword_combos k ON k.id=t.combo_id SET
            t.impressions=(SELECT IFNULL(SUM(impressions),0) FROM gsc_daily g WHERE g.page=CONCAT(%s,k.url_path) AND g.`date` BETWEEN t.start_at AND t.end_at),
            t.clicks=(SELECT IFNULL(SUM(clicks),0) FROM gsc_daily g WHERE g.page=CONCAT(%s,k.url_path) AND g.`date` BETWEEN t.start_at AND t.end_at)""",
       (base_url, base_url))


# ── 리드(Phase 8, own_form) ───────────────────────────────────────────────
def insert_lead(**c):
    return ex(f"INSERT INTO leads ({','.join(c)}) VALUES ({','.join(['%s'] * len(c))})", list(c.values()))


def recent_leads_from(ip_hash, minutes=60):
    return q1("SELECT COUNT(*) n FROM leads WHERE ip_hash=%s AND created_at>=NOW()-INTERVAL %s MINUTE", (ip_hash, minutes))["n"]


def lead_forwarded(lead_id, result, external_id):
    ex("UPDATE leads SET forwarded_at=NOW(), forward_result=%s, external_lead_id=%s WHERE id=%s", (J(result), external_id, lead_id))


def leads(limit=200):
    return q("SELECT l.*, o.advertiser FROM leads l JOIN offers o ON o.id=l.offer_id ORDER BY l.id DESC LIMIT %s", (limit,))


def lead(lead_id):
    return q1("SELECT * FROM leads WHERE id=%s", (lead_id,))


def purge_leads():
    """보유기간이 지난 리드의 개인정보를 지운다(행은 통계용으로 남긴다). 지운 건수를 돌려준다."""
    n = q1("SELECT COUNT(*) n FROM leads WHERE retention_until<CURDATE() AND purged_at IS NULL")["n"]
    ex("UPDATE leads SET name_enc=NULL, phone_enc=NULL, fields=NULL, purged_at=NOW() WHERE retention_until<CURDATE() AND purged_at IS NULL")
    return n


# ── 실데이터 적재(Phase 2) ────────────────────────────────────────────────
def update_region_court(path_slug, row):
    cols = ["court_name", "court_branch", "court_address", "court_phone", "court_url"]
    ex(f"UPDATE regions SET {','.join(c + '=%s' for c in cols)} WHERE path_slug=%s", [row.get(c) or None for c in cols] + [path_slug])


def upsert_region_fact(path_slug, row):
    ex("""REPLACE INTO region_facts (region_id, `key`, `value`, source, as_of)
          SELECT id, %s, %s, %s, %s FROM regions WHERE path_slug=%s""",
       (row["key"], row["value"], row.get("source") or None, row.get("as_of") or None, path_slug))
