import os
from werkzeug.routing import RequestRedirect
from pathlib import Path

from flask import Flask

import db


def _load_env():
    env = Path(__file__).parent / ".env"
    if env.exists():
        for line in env.read_text("utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _chunks(text, n=2):
    """긴 문단을 n문장씩 나눈다(화면 가독성용). 문장 끝(. ! ?) 뒤 공백에서만 끊어 0.03 같은 숫자는 건드리지 않는다."""
    import re
    parts = [p for p in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if p]
    return [" ".join(parts[i:i + n]) for i in range(0, len(parts), n)]


def _pick(seq, n, seed):
    """seq 에서 n개를 seed 로 결정적으로 고른다(원래 순서 유지). 같은 페이지는 항상 같은 조합."""
    import random
    seq = list(seq or [])
    if len(seq) <= n:
        return seq
    keep = set(random.Random(str(seed)).sample(range(len(seq)), n))
    return [x for i, x in enumerate(seq) if i in keep]


def _josa(word, a, b):
    """받침 있으면 a, 없으면 b (을/를, 은/는, 이/가)."""
    w = (word or "").strip()
    last = w[-1] if w else ""
    return w + (a if "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 else b)


def create_app():
    _load_env()
    app = Flask(__name__)
    RequestRedirect.code = 301  # 끝 슬래시 보정을 308 대신 301 로(네이버가 308 을 '리다이렉션 수집제한'으로 본다)
    env = os.environ
    app.config.update(
        SECRET_KEY=env["SECRET_KEY"],
        DB_HOST=env.get("DB_HOST", "127.0.0.1"), DB_PORT=env.get("DB_PORT", "3306"), DB_USER=env.get("DB_USER", "root"),
        DB_PASSWORD=env.get("DB_PASSWORD", ""), DB_NAME=env.get("DB_NAME", "leadsite"),
        BASE_URL=env.get("BASE_URL", "http://localhost:5000").rstrip("/"),
        ADMIN_USER=env.get("ADMIN_USER", ""), ADMIN_PASSWORD=env.get("ADMIN_PASSWORD", ""),
        ADMIN_IPS=[ip.strip() for ip in env.get("ADMIN_IPS", "").split(",") if ip.strip()],
        OWN_FORM_ENABLED=env.get("OWN_FORM_ENABLED", "false").lower() == "true",  # 기본 false. 법무 검토 전에는 켜지 말 것
        LEAD_KEY=env.get("LEAD_KEY", ""),  # 리드 연락처 암호화 키(Fernet). `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`
        SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=env.get("BASE_URL", "").startswith("https"),
    )
    app.teardown_appcontext(db.close)
    app.jinja_env.filters["fromjson"] = lambda v: db.js(v, {})
    app.jinja_env.filters["chunks"] = _chunks
    app.jinja_env.filters["pick"] = _pick
    app.jinja_env.filters["josa"] = _josa
    from og import card_path
    app.jinja_env.filters["og_card"] = card_path

    from admin import admin_bp
    from jobs import jobs_cli
    from web import go_bp, lead_bp, postback_bp, seo_bp, web_bp
    for bp in (seo_bp, go_bp, postback_bp, lead_bp, admin_bp, web_bp):
        app.register_blueprint(bp)
    app.cli.add_command(jobs_cli)
    return app
