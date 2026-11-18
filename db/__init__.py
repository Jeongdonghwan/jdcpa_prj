"""DB 접근 헬퍼. ORM 없이 PyMySQL + SQL 함수(db/queries.py)."""
import json

import pymysql
from flask import current_app, g


def connect(cfg, database=True, **kw):
    return pymysql.connect(
        host=cfg["DB_HOST"], port=int(cfg["DB_PORT"]), user=cfg["DB_USER"], password=cfg["DB_PASSWORD"],
        database=cfg["DB_NAME"] if database else None, charset="utf8mb4", autocommit=True,
        cursorclass=pymysql.cursors.DictCursor, **kw)


def conn():
    if "db" not in g:
        g.db = connect(current_app.config)
    return g.db


def close(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def q(sql, args=()):
    with conn().cursor() as cur:
        cur.execute(sql, args)
        return list(cur.fetchall())


def q1(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql, args=()):
    with conn().cursor() as cur:
        cur.execute(sql, args)
        return cur.lastrowid


def js(value, default=None):
    """MariaDB JSON 컬럼(LONGTEXT)은 문자열로 온다."""
    return json.loads(value) if value else default
