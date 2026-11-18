"""공개 라이선스 사진 후보 찾기(위키미디어 공용 공식 API). 퍼블릭 도메인·CC0 만 고른다(출처 표기 의무 없음).

    python -I scripts/find_photos.py search <작업폴더>        # 후보 썸네일을 받고 번호 붙은 모아보기(sheet.jpg)를 만든다
    python -I scripts/find_photos.py pick <작업폴더> law=3,17 dui=22 ...   # 고른 번호를 static/photos/{업종}-{n}.webp 로 저장 + data/photos.json(출처 기록)

PIL 필요(프로젝트 venv).
"""
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
API = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "site-photo-research/1.0 (local build script)"}
QUERIES = {
    "law": ["courthouse columns", "gavel", "law books library", "signing document pen"],
    "dui": ["road night traffic lights", "car keys", "highway night"],
    "baby": ["newborn baby feet", "baby shoes", "pregnancy ultrasound image"],
    "car": ["car showroom", "car dashboard interior", "parking lot cars rows"],
    "demol": ["interior demolition renovation", "construction worker interior wall removal", "empty commercial space renovation"],
    "move": ["moving boxes cardboard", "moving truck furniture"],
    "clean": ["window cleaning squeegee", "empty apartment room", "cleaning supplies bucket"],
    "pipe": ["plumber sink pipes", "pipe wrench plumbing", "kitchen sink drain"],
}
OK = ("public domain", "cc0", "pd")


def _open(url, timeout):
    """요청 사이에 쉬고, 429(요청 과다)면 기다렸다 다시."""
    for wait in (2, 8, 20, 45):
        time.sleep(1.5)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(wait)
    raise RuntimeError("요청 과다로 중단: " + url)


def api(**params):
    return json.loads(_open(API + "?" + urllib.parse.urlencode({"format": "json", **params}), 30))


def fetch(url):
    return _open(url, 60)


def search(work):
    work.mkdir(parents=True, exist_ok=True)
    cands = []
    for key, queries in QUERIES.items():
        for q in queries:
            d = api(action="query", generator="search", gsrnamespace=6, gsrlimit=14, gsrsearch=q + " filetype:bitmap",
                    prop="imageinfo", iiprop="url|extmetadata|size", iiurlwidth=320)
            for p in d.get("query", {}).get("pages", {}).values():
                ii = p["imageinfo"][0]
                lic = ii["extmetadata"].get("LicenseShortName", {}).get("value", "")
                if not any(x in lic.lower() for x in OK) or ii.get("width", 0) < 1200 or ii["width"] < ii["height"]:
                    continue
                cands.append({"key": key, "title": p["title"], "license": lic, "thumb": ii["thumburl"], "page": ii["descriptionurl"],
                              "artist": ii["extmetadata"].get("Artist", {}).get("value", "")[:200]})
    seen, out = set(), []
    for c in cands:
        if c["title"] not in seen:
            seen.add(c["title"])
            out.append(c)
    cols, tw, th = 10, 200, 150
    sheet = Image.new("RGB", (cols * tw, ((len(out) + cols - 1) // cols) * th), "white")
    draw = ImageDraw.Draw(sheet)
    keep = []
    for c in out:
        try:
            im = Image.open(io.BytesIO(fetch(c["thumb"]))).convert("RGB")
        except Exception:
            continue
        i = len(keep)
        im.thumbnail((tw, th))
        x, y = (i % cols) * tw, (i // cols) * th
        sheet.paste(im, (x, y))
        draw.rectangle([x, y, x + 66, y + 16], fill="black")
        draw.text((x + 3, y + 2), f"{i} {c['key']}", fill="white")
        keep.append(c)
    sheet.save(work / "sheet.jpg", quality=80)
    (work / "cands.json").write_text(json.dumps(keep, ensure_ascii=False, indent=1), "utf-8")
    print(len(keep), "candidates", sheet.size)


def pick(work, args):
    cands = json.loads((work / "cands.json").read_text("utf-8"))
    out_dir = ROOT / "static" / "photos"
    out_dir.mkdir(parents=True, exist_ok=True)
    record = {}
    for a in args:
        key, nums = a.split("=")
        for n, idx in enumerate(nums.split(","), 1):
            c = cands[int(idx)]
            d = api(action="query", titles=c["title"], prop="imageinfo", iiprop="url", iiurlwidth=1280)
            url = next(iter(d["query"]["pages"].values()))["imageinfo"][0]["thumburl"]
            im = Image.open(io.BytesIO(fetch(url))).convert("RGB")
            w, h = im.size
            target = w * 9 // 16                       # 16:9 로 가운데 자르기
            if h > target:
                top = (h - target) // 2
                im = im.crop((0, top, w, top + target))
            im.save(out_dir / f"{key}-{n}.webp", "WEBP", quality=80)
            record.setdefault(key, []).append({"file": f"/static/photos/{key}-{n}.webp", "title": c["title"], "license": c["license"],
                                               "source": c["page"], "size": list(im.size)})
            print(key, n, im.size, c["license"])
    (ROOT / "data" / "photos.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    if sys.argv[1] == "search":
        search(Path(sys.argv[2]))
    else:
        pick(Path(sys.argv[2]), sys.argv[3:])
