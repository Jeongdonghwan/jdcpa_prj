"""관리자: 범용 CRUD + 검수·대시보드·CSV 적재·정리 큐·리드. 로그인 + IP 제한 + audit_log."""
import hmac
import json
import secrets

from flask import Blueprint, abort, current_app, redirect, render_template, request, session, url_for

import jobs
import pipeline
from db import js, ops, queries as dbq
from engine import compliance_ok, gate, lint_text

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

# 편집 가능한 컬럼 화이트리스트. json = JSON 검증 대상, lint = 금지 표현 검사 대상
TABLES = {
    "networks": {
        "label": "네트워크", "list": ["slug", "name", "kind", "is_active"],
        "cols": ["slug", "name", "kind", "adapter_class", "conventions", "postback", "csv_mapping", "currency", "settlement_note", "is_active"],
        "json": ["conventions", "postback", "csv_mapping"], "lint": []},
    "offers": {
        "label": "오퍼", "list": ["slug", "advertiser", "integration_mode", "status", "compliance_ok"],
        "cols": ["network_id", "vertical_id", "slug", "advertiser", "name", "category", "payout_type", "payout_amount",
                 "integration_mode", "tracking_url_template", "embed_url_template", "script_snippet", "phone", "kakao_url", "forward_config",
                 "region_scope", "topic_scope", "start_at", "end_at", "daily_cap", "monthly_cap", "manual_weight", "compliance_fields",
                 "blurb", "badge", "status"],
        "json": ["region_scope", "topic_scope", "compliance_fields", "forward_config"], "lint": ["advertiser", "name", "blurb", "badge"]},
    "offer_placements": {
        "label": "플레이스먼트", "list": ["offer_id", "scope_type", "scope_id", "slot", "mode", "weight", "position"],
        "cols": ["offer_id", "scope_type", "scope_id", "slot", "mode", "weight", "position"], "json": [], "lint": []},
    "ab_tests": {
        "label": "A/B 테스트", "list": ["name", "scope_type", "scope_id", "slot", "status", "winner"],
        "cols": ["name", "scope_type", "scope_id", "slot", "arms", "start_at", "end_at", "status", "winner"], "json": ["arms"], "lint": []},
    "regions": {
        "label": "지역", "list": ["path_slug", "full_name", "level", "court_name", "publish_stage"],
        "cols": ["parent_id", "level", "code", "slug", "short_name", "full_name", "path_slug", "path_name", "court_name",
                 "court_branch", "court_address", "court_phone", "court_url", "publish_stage", "sort_order"],
        "json": [], "lint": []},
    "region_aliases": {
        "label": "지역 별칭", "list": ["region_id", "alias_type", "name", "slug"],
        "cols": ["region_id", "alias_type", "name", "slug"], "json": [], "lint": []},
    "verticals": {
        "label": "버티컬", "list": ["slug", "name", "url_base", "is_active"],
        "cols": ["site_id", "slug", "name", "short_label", "url_base", "compliance_schema", "trust_bullets", "cautions", "procedure_tabs",
                 "personas", "keyword_template", "fallback_offer_id", "form_offer_id", "is_active"],
        "json": ["compliance_schema", "trust_bullets", "cautions", "procedure_tabs", "personas"], "lint": ["trust_bullets", "cautions"]},
    "keyword_terms": {
        "label": "키워드 용어", "list": ["vertical_id", "dimension_id", "term", "slug", "canonical_term_id", "volume_pc", "volume_mobile"],
        "cols": ["vertical_id", "dimension_id", "term", "slug", "canonical_term_id", "is_active"], "json": [], "lint": ["term"]},
    "keyword_combos": {
        "label": "콤보", "list": ["page_type", "primary_keyword", "url_path", "status", "volume_total", "priority_score", "cannibal_group_id"],
        "cols": ["primary_keyword", "url_path", "parent_combo_id", "cannibal_group_id", "is_group_rep", "status"], "json": [], "lint": ["primary_keyword"]},
    "title_tests": {
        "label": "제목 테스트", "list": ["combo_id", "variant_title", "start_at", "end_at", "impressions", "clicks", "status"],
        "cols": ["combo_id", "variant_title", "variant_desc", "start_at", "end_at", "status"], "json": [], "lint": ["variant_title", "variant_desc"]},
    "authors": {
        "label": "작성자", "list": ["slug", "name", "role"], "cols": ["slug", "name", "role", "bio", "links"], "json": ["links"], "lint": ["bio"]},
    "network_sites": {
        "label": "추천 사이트", "list": ["name", "url", "vertical_tag", "rel", "is_active"],
        "cols": ["name", "url", "vertical_tag", "rel", "is_active"], "json": [], "lint": []},
}
PAGES = {"review": "콘텐츠 검수", "dashboard": "대시보드", "imports": "CSV 적재", "queue": "정리 후보", "kw_review": "미분류 키워드", "leads": "리드"}


@admin_bp.context_processor
def menu():
    return {"tables": TABLES, "pages": PAGES}


@admin_bp.before_request
def guard():
    cfg = current_app.config
    if cfg["ADMIN_IPS"] and request.remote_addr not in cfg["ADMIN_IPS"]:
        abort(403)
    if not cfg["ADMIN_PASSWORD"]:  # 비밀번호 미설정이면 관리자 전체 비활성
        abort(403)
    if request.endpoint != "admin.login" and not session.get("admin"):
        return redirect(url_for("admin.login"))
    if request.method == "POST" and request.endpoint != "admin.login":
        if not hmac.compare_digest(request.form.get("csrf", ""), session.get("csrf", "-")):
            abort(400)
    return None


@admin_bp.after_request
def no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


def _audit(action, target, diff=None):
    dbq.audit(session["admin"], action, target, json.dumps(diff or {}, ensure_ascii=False, default=str))


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    # ponytail: 로그인 시도 제한 없음. ADMIN_IPS 또는 nginx allow 로 막고, 외부 공개가 필요해지면 rate limit 추가
    error = None
    if request.method == "POST":
        cfg = current_app.config
        ok = hmac.compare_digest(request.form.get("user", "").encode(), cfg["ADMIN_USER"].encode()) & \
            hmac.compare_digest(request.form.get("password", "").encode(), cfg["ADMIN_PASSWORD"].encode())
        if ok:
            session.clear()
            session.update(admin=cfg["ADMIN_USER"], csrf=secrets.token_hex(16))
            return redirect(url_for("admin.index"))
        error = "아이디 또는 비밀번호가 맞지 않습니다."
    return render_template("admin/login.html", error=error)


@admin_bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


@admin_bp.get("/")
def index():
    return render_template("admin/list.html", table=None)


# ── 검수(Phase 2) ─────────────────────────────────────────────────────────
@admin_bp.route("/review", methods=["GET", "POST"])
def review():
    message = None
    if request.method == "POST":  # 승인분 공개 실행(게이트·주간 상한 적용)
        results = pipeline.publish_ready(dbq.site())
        _audit("publish_ready", "keyword_combos", {"results": results})
        message = f"공개 {sum(1 for _u, f in results if not f)}건, 게이트 미통과 {sum(1 for _u, f in results if f)}건"
    return render_template("admin/review.html", rows=ops.review_queue(), message=message)


@admin_bp.route("/review/<int:content_id>", methods=["GET", "POST"])
def review_one(content_id):
    content = ops.content_by_id(content_id) or abort(404)
    combo, site = dbq.combo(content["combo_id"]), dbq.site()
    if request.method == "POST":
        if request.form.get("action") == "approve":
            if not js(content["lint_result"], {}).get("ok"):
                abort(400)  # 린트 미통과 콘텐츠는 승인할 수 없다(직접 수정 후 재채점)
            pipeline.approve(site, content_id, session["admin"])
        else:
            pipeline.reject(content_id, session["admin"])
        _audit(f"content_{request.form.get('action')}", f"page_contents:{content_id}")
        return redirect(url_for("admin.review"))
    return render_template("admin/review_one.html", c=content, combo=combo, blocks=js(content["blocks"], []), faqs=js(content["faqs"], []),
                           lint=js(content["lint_result"], {}), flags=js(content["fact_flags"], []),
                           gate=gate(site, combo) if content["status"] == "approved" else None)


# ── 대시보드(Phase 3) ─────────────────────────────────────────────────────
@admin_bp.get("/dashboard")
def dashboard():
    group = request.args.get("group", "offer")
    if group not in ("offer", "combo", "region", "slot"):
        abort(404)
    return render_template("admin/dashboard.html", group=group, rows=ops.dashboard(group))


# ── CSV 적재(Phase 2·3·4·6) ───────────────────────────────────────────────
@admin_bp.route("/imports", methods=["GET", "POST"])
def imports():
    message = None
    if request.method == "POST":
        f = request.files.get("file")
        try:
            message = jobs.run_import(request.form.get("kind", ""), f.read().decode("utf-8-sig") if f else "", dbq.site(),
                                      request.form.get("arg") or None)
            _audit("import", request.form.get("kind", ""), {"result": message})
        except (ValueError, KeyError, UnicodeDecodeError) as e:
            message = f"실패: {type(e).__name__}: {e}"
    return render_template("admin/imports.html", message=message, formats=ops.IMPORTS)


# ── 큐: 정리 후보(Phase 6)·미분류 키워드(Phase 4) ─────────────────────────
@admin_bp.route("/queue", methods=["GET", "POST"], defaults={"job": "prune"}, endpoint="queue")
@admin_bp.route("/kw_review", methods=["GET", "POST"], defaults={"job": "kw_review"}, endpoint="kw_review")
def queue(job):
    if request.method == "POST":
        row = ops.job(request.form.get("id", type=int)) or abort(404)
        payload, action = json.loads(row["payload"]), request.form.get("action")
        if row["job"] == "prune" and action == "approve":  # 승인 → merged → 부모로 301
            ops.set_combo(payload["combo_id"], status="merged", retired_at=None)
            dbq.bump_cache_version(dbq.site()["id"])
        ops.finish_job(row["id"], "done", f"{action} by {session['admin']}")
        _audit(f"{row['job']}_{action}", f"job_queue:{row['id']}", payload)
        return redirect(request.path)
    rows = [{**r, "payload": json.loads(r["payload"])} for r in ops.queued(job)]
    return render_template("admin/queue.html", job=job, rows=rows)


# ── 리드(Phase 8) ─────────────────────────────────────────────────────────
@admin_bp.get("/leads")
def leads():
    cfg, rows = current_app.config, []
    if cfg["OWN_FORM_ENABLED"] and cfg["LEAD_KEY"]:
        from cryptography.fernet import Fernet
        f = Fernet(cfg["LEAD_KEY"].encode())
        for r in ops.leads():
            name = f.decrypt(r["name_enc"]).decode() if r["name_enc"] else ""
            phone = f.decrypt(r["phone_enc"]).decode() if r["phone_enc"] else ""
            rows.append({**r, "name": name[:1] + "*" * max(len(name) - 1, 0), "phone": "*" * max(len(phone) - 4, 0) + phone[-4:],
                         "forward": js(r["forward_result"], {})})
        _audit("leads_view", "leads", {"count": len(rows)})  # 접근 기록
    return render_template("admin/leads.html", rows=rows, enabled=cfg["OWN_FORM_ENABLED"])


# ── 범용 CRUD ─────────────────────────────────────────────────────────────
@admin_bp.get("/t/<table>/")
def rows(table):
    t = TABLES.get(table) or abort(404)
    return render_template("admin/list.html", table=table, t=t, rows=dbq.admin_list(table, t["list"]))


@admin_bp.route("/t/<table>/new", methods=["GET", "POST"], defaults={"row_id": None})
@admin_bp.route("/t/<table>/<int:row_id>", methods=["GET", "POST"])
def edit(table, row_id):
    t = TABLES.get(table) or abort(404)
    row = dbq.admin_get(table, row_id) if row_id else {}
    if row is None:
        abort(404)
    errors = []
    if request.method == "POST":
        data = {c: (request.form.get(c, "").strip() or None) for c in t["cols"]}
        for c in t["json"]:
            try:
                data[c] and json.loads(data[c])
            except ValueError:
                errors.append(f"{c}: JSON 형식이 아닙니다.")
        for c in t["lint"]:
            if lint_text(data[c]):
                errors.append(f"{c}: 금지 표현({', '.join(lint_text(data[c]))})")
        if table == "offers" and not errors:
            vertical = dbq.vertical(data["vertical_id"])
            if not vertical:
                errors.append("vertical_id: 없는 버티컬입니다.")
            else:  # 필수 표기 미충족 오퍼는 저장은 되지만 노출되지 않는다
                data["compliance_ok"] = int(compliance_ok(vertical, js(data["compliance_fields"], {})))
        if table == "verticals" and data["is_active"] == "1" and not (row_id and dbq.eligible_offers(row_id)):
            errors.append("is_active: 노출 가능한 오퍼가 1개 이상 있어야 버티컬을 활성화할 수 있습니다.")
        if not errors:
            try:
                saved_id = dbq.admin_save(table, row_id, data)
            except Exception as e:  # 중복 slug, NOT NULL 등 DB 제약 오류를 폼에 그대로 보여준다
                errors.append(f"저장 실패: {e}")
            else:
                _audit("update" if row_id else "create", f"{table}:{saved_id}", data)
                dbq.bump_cache_version(dbq.site()["id"])  # 렌더 캐시 무효화
                return redirect(url_for("admin.rows", table=table))
        row = {**row, **data}
    return render_template("admin/form.html", table=table, t=t, row=row, row_id=row_id, errors=errors)
