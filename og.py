"""페이지별 OG 카드(1200×630) — 처음 요청될 때 그려 디스크에 두고 이후엔 파일을 준다(raws_prj 방식).

주제 카드 1장을 수천 쪽이 같이 쓰면 네이버 웹문서 썸네일·카드 묶음(ItemList)이 전부 같은 그림이 된다.
네이버는 og:image 를 정사각형으로 잘라 보여 주므로 글자는 가운데 630×630 안에만 둔다.
18만 장을 미리 만들면 수 GB 라서 로봇·방문자가 실제로 가져가는 것만 만든다. 전화번호는 넣지 않는다(상담 업체가 아님).
"""
import hashlib
import os
from pathlib import Path

DESIGN = "c1"  # 디자인을 바꾸면 올린다 → 주소(?v=)가 바뀌어 캐시도 바뀐다
DIR = Path(__file__).parent / "static" / "og" / "c"
W, H, SQ = 1200, 630, 630
X0 = (W - SQ) // 2 + 40  # 정사각형 안쪽 여백 40
TW = SQ - 80


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


def card_file(combo, label, cta, site_name, theme):
    """카드 파일 경로. 없으면 그린다(임시 파일에 쓰고 rename — 워커 여럿이 동시에 그려도 깨지지 않게)."""
    f = DIR / f"{combo['id']}-{ver(combo['primary_keyword'])}.webp"
    if f.exists():
        return f
    from PIL import Image, ImageDraw, ImageFont
    font_path = os.environ.get("OG_FONT", r"C:\Windows\Fonts\malgunbd.ttf")
    DIR.mkdir(parents=True, exist_ok=True)
    accent, ink, muted = theme.get("accent", "#0e7c86"), theme.get("ink", "#15202b"), theme.get("muted", "#5c6b7a")
    img = Image.new("RGB", (W, H), theme.get("tint", "#eef2f6"))
    dr = ImageDraw.Draw(img)
    dr.rectangle([(W - SQ) // 2, 0, (W + SQ) // 2, H], fill="white")
    small = ImageFont.truetype(font_path, 28)
    for size in (64, 56, 48, 42):  # 키워드가 4줄 안에 들어가는 가장 큰 글자
        big = ImageFont.truetype(font_path, size)
        lines = _wrap(dr, combo["primary_keyword"], big, TW)
        if len(lines) <= 4:
            break
    y = 120
    dr.text((X0, y - 60), label, font=small, fill=accent)
    for ln in lines[:4]:
        dr.text((X0, y), ln, font=big, fill=ink)
        y += big.size + 14
    box_y = max(y + 30, 400)
    dr.rounded_rectangle([X0, box_y, X0 + TW, box_y + 76], radius=6, outline=accent, width=3)
    tw = dr.textlength(cta, font=small)
    dr.text((X0 + (TW - tw) / 2, box_y + 22), cta, font=small, fill=accent)
    dr.text((X0, H - 70), site_name, font=small, fill=muted)
    tmp = f.with_suffix(".tmp")
    img.save(tmp, "WEBP", quality=82)
    os.replace(tmp, f)
    for old in DIR.glob(f"{combo['id']}-*.webp"):  # 문구가 바뀌어 남은 옛 카드
        if old != f:
            old.unlink(missing_ok=True)
    return f
