"""페이지별 OG 카드(1200×630) — 처음 요청될 때 그려 디스크에 두고 이후엔 파일을 준다(raws_prj 방식).

주제 카드 1장을 수천 쪽이 같이 쓰면 네이버 웹문서 썸네일·카드 묶음(ItemList)이 전부 같은 그림이 된다.
네이버는 og:image 를 정사각형으로 잘라 보여 주므로 글자는 가운데 630×630 안에만 둔다.
18만 장을 미리 만들면 수 GB 라서 로봇·방문자가 실제로 가져가는 것만 만든다. 전화번호는 넣지 않는다(상담 업체가 아님).
"""
import hashlib
import os
from pathlib import Path

DESIGN = "c2"  # 디자인을 바꾸면 올린다 → 주소(?v=)가 바뀌어 캐시도 바뀐다
DIR = Path(__file__).parent / "static" / "og" / "c"
W, H, SQ = 1200, 630, 630


def ver(keyword):
    return hashlib.md5((DESIGN + keyword).encode()).hexdigest()[:8]


def card_path(combo):
    """og:image·ItemList 에 넣는 주소(사이트 기준 경로). 여기서는 그리지 않는다."""
    return f"/og/c/{combo['id']}.webp?v={ver(combo['primary_keyword'])}"


def _wrap(dr, text, font, width):
    lines, line = [], ""
    for word in text.split():
        if line and dr.textlength(f"{line} {word}", font=font) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + [line]


def _heavy(font_path):
    """같은 폴더에 ExtraBold 가 있으면 그것(나눔고딕), 없으면 지정 폰트."""
    p = Path(font_path)
    for name in ("NanumGothicExtraBold.ttf", p.name):
        if (p.parent / name).exists():
            return str(p.parent / name)
    return font_path


def card_file(combo, label, site_name, theme):
    """카드 파일 경로. 없으면 그린다(임시 파일에 쓰고 rename — 워커 여럿이 동시에 그려도 깨지지 않게).
    목록 썸네일은 가운데 정사각형만 잘라 보여 주므로 글자는 그 안에서 크게: 분류 → 키워드(최대 3줄) → 짧은 선 + 사이트명."""
    f = DIR / f"{combo['id']}-{ver(combo['primary_keyword'])}.webp"
    if f.exists():
        return f
    from PIL import Image, ImageDraw, ImageFont
    font_path = os.environ.get("OG_FONT", r"C:\Windows\Fonts\malgunbd.ttf")
    heavy = _heavy(font_path)
    DIR.mkdir(parents=True, exist_ok=True)
    accent, ink, muted = theme.get("accent", "#0e7c86"), theme.get("ink", "#15202b"), theme.get("muted", "#5c6b7a")
    img = Image.new("RGB", (W, H), theme.get("tint", "#eef2f6"))
    dr = ImageDraw.Draw(img)
    x0 = (W - SQ) // 2
    dr.rectangle([x0, 0, x0 + SQ, H], fill="white")
    dr.rectangle([x0, 0, x0 + SQ, 12], fill=accent)
    pad = 56
    tw = SQ - pad * 2
    lab = ImageFont.truetype(font_path, 34)
    dr.text((x0 + pad, 64), label, font=lab, fill=accent)
    kw = combo["primary_keyword"]
    for size in (96, 88, 80, 72, 64, 56, 50):  # 3줄 안에 들어가는 가장 큰 글자
        big = ImageFont.truetype(heavy, size)
        lines = _wrap(dr, kw, big, tw)
        if len(lines) <= 3 and all(dr.textlength(ln, font=big) <= tw for ln in lines):
            break
    lh = int(big.size * 1.22)
    top, bottom = 130, 480
    y = top + (bottom - top - lh * len(lines[:3])) // 2
    for ln in lines[:3]:
        dr.text((x0 + pad, y), ln, font=big, fill=ink)
        y += lh
    dr.rectangle([x0 + pad, 512, x0 + pad + 64, 518], fill=accent)
    dr.text((x0 + pad, 540), site_name, font=ImageFont.truetype(font_path, 30), fill=muted)
    tmp = f.with_suffix(".tmp")
    img.save(tmp, "WEBP", quality=82)
    os.replace(tmp, f)
    for old in DIR.glob(f"{combo['id']}-*.webp"):  # 문구가 바뀌어 남은 옛 카드
        if old != f:
            old.unlink(missing_ok=True)
    return f
