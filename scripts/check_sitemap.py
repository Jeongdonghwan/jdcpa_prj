"""실행 중인 서버의 사이트맵 전수 200 검증.  python scripts/check_sitemap.py [BASE_URL]

사이트맵의 <loc> 호스트 대신 인자로 준 BASE_URL 로 요청한다(배포 전 로컬 검증용).
"""
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5000").rstrip("/")


def fetch(path):
    try:
        with urllib.request.urlopen(base + path) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, ""


def locs(xml):
    return [urlsplit(u).path for u in re.findall(r"<loc>(.*?)</loc>", xml)]


status, index = fetch("/sitemap-index.xml")
assert status == 200, f"sitemap-index.xml {status}"
urls = [u for sm in locs(index) for u in locs(fetch(sm)[1])]
bad = [(u, s) for u in urls if (s := fetch(u)[0]) != 200]
print(f"{len(urls)}개 URL 중 {len(urls) - len(bad)}개 200")
for u, s in bad:
    print(" ", s, u)
sys.exit(1 if bad or len(urls) != len(set(urls)) else 0)
