import os
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


def create_app():
    _load_env()
    app = Flask(__name__)
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

    from admin import admin_bp
    from jobs import jobs_cli
    from web import go_bp, lead_bp, postback_bp, seo_bp, web_bp
    for bp in (seo_bp, go_bp, postback_bp, lead_bp, admin_bp, web_bp):
        app.register_blueprint(bp)
    app.cli.add_command(jobs_cli)
    return app
