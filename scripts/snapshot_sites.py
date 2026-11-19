"""SEO 점검용 표본 스냅샷: 실행 중인 로컬 서버에서 사이트별로 메인·robots·사이트맵·RSS·고지 페이지와 무작위 공개 페이지를 받아
<출력>/<라벨>/out/ 에 정적 사이트처럼 저장한다. 그 폴더에 site-launch 스킬의 check_all.py 를 그대로 돌린다.

    python scripts/snapshot_sites.py <출력 폴더> [표본수=60] [서버=http://127.0.0.1:5000]   (프로젝트 venv)
"""
import os
import random
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app  # noqa: E402
import db  # noqa: E402

OUT = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 60
BASE = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:5000"
EXTRA = ["/", "/robots.txt", "/rss.xml", "/llms.txt", "/about/", "/광고고지/", "/운영사/", "/사이트맵/"]


def fetch(host, path):
    req = urllib.request.Request(BASE + urllib.parse.quote(path), headers={"Host": host, "User-Agent": "Mozilla/5.0 snapshot"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def save(root, path, body):
    p = urllib.parse.unquote(path).strip("/")
    if path.endswith((".txt", ".xml")):
        f = os.path.join(root, p)
    else:
        f = os.path.join(root, p, "index.html") if p else os.path.join(root, "index.html")
    os.makedirs(os.path.dirname(f), exist_ok=True)
    open(f, "wb").write(body)


def main():
    app = create_app()
    with app.app_context():
        sites = db.q("SELECT id, host FROM sites WHERE id>1 ORDER BY id")
        for s in sites:
            label = s["host"].split(".")[0]
            host = f"{label}.localhost:5000"
            root = os.path.join(OUT, label, "out")
            rows = db.q("SELECT url_path FROM keyword_combos WHERE site_id=%s AND status='published' ORDER BY RAND() LIMIT %s", (s["id"], N))
            for path in EXTRA + [r["url_path"] for r in rows]:
                save(root, path, fetch(host, path))
            # 사이트맵: 운영에서는 export/<라벨>/ 의 파일을 nginx 가 내준다 → 그 파일을 쓰되, 주소는 표본에 있는 것만 남긴다
            locs = "".join(f"<url><loc>https://{s['host']}{urllib.parse.quote(p)}</loc></url>\n" for p in ["/"] + [r["url_path"] for r in rows])
            open(os.path.join(root, "sitemap.xml"), "w", encoding="utf-8").write(
                f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{locs}</urlset>\n')
            print(label, len(rows) + len(EXTRA), "files", flush=True)


if __name__ == "__main__":
    main()
