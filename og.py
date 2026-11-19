"""페이지별 OG 카드(1200×630) — 처음 요청될 때 그려 디스크에 두고 이후엔 파일을 준다(raws_prj 방식).

주제 카드 1장을 수천 쪽이 같이 쓰면 네이버 웹문서 썸네일·카드 묶음(ItemList)이 전부 같은 그림이 된다.
네이버는 og:image 를 정사각형으로 잘라 보여 주므로 글자는 가운데 630×630 안에만 둔다.
18만 장을 미리 만들면 수 GB 라서 로봇·방문자가 실제로 가져가는 것만 만든다. 전화번호는 넣지 않는다(상담 업체가 아님).
"""
import hashlib
import os
from pathlib import Path

DESIGN = "c3"  # 디자인을 바꾸면 올린다 → 주소(?v=)가 바뀌어 캐시도 바뀐다
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


FONTS = Path(__file__).parent / "fonts"  # Pretendard(OFL) — 서버 폰트와 상관없이 같은 모양


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _tag(page_type):
    """분류 태그·장식 글자: 질문 글 Q&A(색 채운 카드), 지역 페이지 지역, 나머지 해설(흰 카드)."""
    if page_type == "question":
        return "Q&A", "Q"
    return ("지역" if page_type.startswith("region") or page_type == "court_alias" else "해설"), "“"


def card_file(combo, label, site_name, theme):
    """카드 파일 경로. 없으면 그린다(임시 파일에 쓰고 rename — 워커 여럿이 동시에 그려도 깨지지 않게).
    목록·네이버 썸네일은 가운데 정사각형만 보이므로 모든 글자는 그 안에: 태그·분류 → 키워드(최대 3줄, 마지막 줄 형광펜) → 사이트명."""
    f = DIR / f"{combo['id']}-{ver(combo['primary_keyword'])}.webp"
    if f.exists():
        return f
    from PIL import Image, ImageDraw, ImageFont
    DIR.mkdir(parents=True, exist_ok=True)
    black, bold = str(FONTS / "Pretendard-Black.otf"), str(FONTS / "Pretendard-Bold.otf")
    tag, glyph = _tag(combo.get("page_type") or "")
    filled = tag == "Q&A"
    A, white, ink = _rgb(theme.get("accent", "#0e7c86")), (255, 255, 255), (21, 32, 43)
    if filled:
        bg, card, fg, sub = _mix(A, (0, 0, 0), .12), A, white, _mix(white, A, .35)
    else:
        bg, card, fg, sub = _mix(A, white, .86), white, ink, _mix(ink, white, .45)
    img = Image.new("RGB", (W, H), bg)
    dr = ImageDraw.Draw(img, "RGBA")
    x0, pad = (W - SQ) // 2, 64
    tw = SQ - pad * 2
    if filled:
        dr.rectangle([x0, 0, x0 + SQ, H], fill=card)
    else:
        dr.rounded_rectangle([x0 + 24, 24, x0 + SQ - 24, H - 24], 28, fill=card)
    dr.text((x0 + SQ - 50, 40), glyph, font=ImageFont.truetype(black, 330),
            fill=_mix(card, white, .14) if filled else _mix(card, A, .09), anchor="rt")
    tf = ImageFont.truetype(bold, 30)
    tl = dr.textlength(tag, font=tf)
    dr.rectangle([x0 + pad, 70, x0 + pad + tl + 32, 122], fill=white if filled else A)
    dr.text((x0 + pad + 16, 96), tag, font=tf, fill=A if filled else white, anchor="lm")
    dr.text((x0 + pad + tl + 52, 96), label, font=tf, fill=sub, anchor="lm")
    kw = combo["primary_keyword"]
    for size in (104, 96, 88, 80, 72, 64, 56, 50):  # 3줄 안에 들어가는 가장 큰 글자
        big = ImageFont.truetype(black, size)
        lines = _wrap(dr, kw, big, tw)
        if len(lines) <= 3 and all(dr.textlength(ln, font=big) <= tw for ln in lines):
            break
    lines = lines[:3]
    lh = int(size * 1.2)
    y = 150 + (340 - lh * len(lines)) // 2
    for i, ln in enumerate(lines):
        if i == len(lines) - 1:
            w = dr.textlength(ln, font=big)
            dr.rectangle([x0 + pad - 6, y + size * .62, x0 + pad + w + 6, y + size * 1.08],
                         fill=white + (60,) if filled else A + (70,))
        dr.text((x0 + pad, y), ln, font=big, fill=fg)
        y += lh
    dr.ellipse([x0 + pad, 528, x0 + pad + 28, 556], fill=white if filled else A)
    dr.text((x0 + pad + 42, 542), site_name, font=tf, fill=sub, anchor="lm")
    tmp = f.with_suffix(".tmp")
    img.save(tmp, "WEBP", quality=82)
    os.replace(tmp, f)
    for old in DIR.glob(f"{combo['id']}-*.webp"):  # 문구가 바뀌어 남은 옛 카드
        if old != f:
            old.unlink(missing_ok=True)
    return f
