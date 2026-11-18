"""서브도메인 사이트 점검(로컬·운영 공통). 사이트마다 메인·허브·주제·시도·시군구 페이지를 받아 기본 항목을 확인한다.

    python -I scripts/check_sites.py [http://127.0.0.1:5000] [도메인 접미=localhost:5000]

확인: 200, title 60자 이내·description 50~160자, canonical 이 자기 호스트, h1 1개, 광고 고지, /go/ 링크의 rel, 업체명이 제목·설명·h1 에 없는지,
      금지 표현(업종별), JSON-LD 파싱, robots·사이트맵·RSS.
"""
import json
import re
import sys
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"
SUFFIX = sys.argv[2] if len(sys.argv) > 2 else "localhost:5000"
SITES = {
    "law": ["/", "/개인회생/", "/개인회생/면책/", "/개인회생/지역/경기/", "/개인회생/지역/경기-수원시-팔달구/", "/개인회생/지역/인천-부평구-부평동/"],
    "dui": ["/", "/음주운전/", "/음주운전/음주운전-면허취소/", "/음주운전/지역/서울/", "/음주운전/지역/경기-안산시-단원구/"],
    "baby": ["/", "/태아보험/", "/태아보험/태아보험-가입시기/", "/태아보험/지역/부산/", "/태아보험/지역/서울-강남구/"],
    "car": ["/", "/장기렌트/", "/장기렌트/법인-장기렌트/", "/장기렌트/지역/대구/", "/중고차/", "/중고차/지역/경기-수원시/"],
    "demol": ["/", "/철거/", "/철거/상가-원상복구/", "/철거/지역/서울/", "/철거/지역/서울-마포구/"],
    "life": ["/", "/이사/", "/이사/지역/경기-고양시/", "/입주청소/", "/입주청소/지역/서울-송파구/", "/배관/", "/배관/누수탐지/", "/배관/지역/서울-강남구/"],
}
# 본문·제목에 나오면 안 되는 업체명(업체 교체 대비: 상호는 오퍼 블록에만)
BRANDS = ["서앤율", "청하", "파로스", "동윤", "레이", "대청", "아이엠카", "중카", "무촌", "다이사", "모두이사", "24번가", "뚫뚫"]


def get(host, path):
    req = urllib.request.Request(BASE + urllib.parse.quote(path), headers={"Host": host, "User-Agent": "Mozilla/5.0 check_sites"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def main():
    bad, n = [], 0
    for sub, paths in SITES.items():
        host = f"{sub}.{SUFFIX}"
        for extra in ("/robots.txt", "/sitemap-index.xml", "/rss.xml", "/사이트맵/", "/광고고지/"):
            code, _ = get(host, extra)
            if code != 200:
                bad.append(f"{sub}{extra}: {code}")
        for path in paths:
            n += 1
            code, html = get(host, path)
            where = f"{sub}{path}"
            if code != 200:
                bad.append(f"{where}: HTTP {code}")
                continue
            title = (re.search(r"<title>(.*?)</title>", html, re.S) or [None, ""])[1]
            desc = (re.search(r'<meta name="description" content="([^"]*)"', html) or [None, ""])[1]
            canon = (re.search(r'<link rel="canonical" href="([^"]*)"', html) or [None, ""])[1]
            h1 = re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.S)
            if not 10 <= len(title) <= 60:
                bad.append(f"{where}: title {len(title)}자")
            if not 50 <= len(desc) <= 160:
                bad.append(f"{where}: description {len(desc)}자")
            if f"//{host}" not in canon:
                bad.append(f"{where}: canonical {canon}")
            if len(h1) != 1:
                bad.append(f"{where}: h1 {len(h1)}개")
            if "광고 안내" not in html:
                bad.append(f"{where}: 광고 고지 없음")
            for a in re.findall(r"<a [^>]*>", html):
                if 'href="/go/' in a and 'rel="sponsored nofollow noopener"' not in a:
                    bad.append(f"{where}: /go/ 링크 rel 누락")
                    break
                if re.search(r'href="https?://(?!' + re.escape(host) + r')', a) and "replyalba" in a:
                    bad.append(f"{where}: 광고 링크가 /go/ 를 거치지 않음")
                    break
            head = title + desc + "".join(h1)
            for b in BRANDS:
                if b in head:
                    bad.append(f"{where}: 제목·설명·h1 에 업체명 {b}")
            for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
                try:
                    json.loads(m)
                except ValueError:
                    bad.append(f"{where}: JSON-LD 오류")
            if path != "/" and "/go/" not in html:
                bad.append(f"{where}: 신청 링크 없음")
    print(f"{n}쪽 점검, 문제 {len(bad)}건")
    for b in bad:
        print(" -", b)
    return 1 if bad else 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(main())
