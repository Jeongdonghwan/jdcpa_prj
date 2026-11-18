"""글 표지 그래픽 생성: static/covers/{사이트}-{1..6}.webp (1200x520).

사진이 없어서 사이트 색으로 만든 도형 그래픽을 쓴다(스톡 사진 없음). 실제 사진이 생기면 같은 파일 이름으로 덮어쓰면 된다.

    python -I scripts/make_covers.py     (PIL 필요 — 프로젝트 venv 로 실행)
"""
import random
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "static" / "covers"
W, H = 1200, 520
THEMES = {  # (짙은 바탕, 강조, 옅은 면)
    "law": ("#0f2742", "#0e7c86", "#dfe8f0"), "dui": ("#161616", "#8e1b2e", "#e3e5e9"), "baby": ("#1f2a44", "#ff5a5f", "#eaf4ff"),
    "car": ("#0a1628", "#2563eb", "#e6ebf5"), "demol": ("#0c121b", "#ffc20e", "#e9edf1"), "life": ("#23211c", "#ff6b1a", "#eceae4"),
    "default": ("#0f2742", "#0e7c86", "#f1f4f7"),
}


def rgb(h, a=255):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) + (a,)


def cover(theme, n):
    dark, accent, soft = THEMES[theme]
    rnd = random.Random(f"{theme}-{n}")
    img = Image.new("RGBA", (W, H), rgb(dark))
    d = ImageDraw.Draw(img, "RGBA")
    for x in range(0, W, 60):                         # 세로 눈금
        d.line([(x, 0), (x, H)], fill=(255, 255, 255, 12), width=1)
    kind = n % 6
    if kind == 0:      # 큰 사선 띠
        d.polygon([(W * .52, 0), (W * .78, 0), (W * .46, H), (W * .20, H)], fill=rgb(accent))
        d.polygon([(W * .82, 0), (W * .90, 0), (W * .58, H), (W * .50, H)], fill=rgb(soft, 60))
    elif kind == 1:    # 계단 블록
        for i in range(6):
            x0 = 120 + i * 160
            d.rectangle([x0, H - 70 - i * 62, x0 + 150, H], fill=rgb(accent, 255 if i % 2 == 0 else 120))
    elif kind == 2:    # 겹친 원
        for i, r in enumerate((300, 220, 140)):
            d.ellipse([W * .68 - r, H * .5 - r, W * .68 + r, H * .5 + r], outline=rgb(accent), width=10 if i else 22)
        d.rectangle([90, H * .62, 420, H * .62 + 12], fill=rgb(soft, 200))
    elif kind == 3:    # 격자 타일
        for gx in range(8):
            for gy in range(4):
                if rnd.random() < .45:
                    x0, y0 = 520 + gx * 82, 60 + gy * 100
                    d.rectangle([x0, y0, x0 + 70, y0 + 88], fill=rgb(accent, rnd.choice((255, 150, 70))))
    elif kind == 4:    # 가로 막대(길이 다른 줄)
        for i in range(7):
            ln = rnd.randint(260, 760)
            d.rectangle([W - 90 - ln, 70 + i * 58, W - 90, 70 + i * 58 + 26], fill=rgb(accent if i % 3 else soft, 255 if i % 3 else 90))
    else:              # 모서리 사분원 + 선
        d.pieslice([W - 520, H - 520, W + 520, H + 520], 180, 270, fill=rgb(accent))
        d.pieslice([W - 300, H - 300, W + 300, H + 300], 180, 270, fill=rgb(dark))
        d.line([(90, H * .3), (560, H * .3)], fill=rgb(soft, 200), width=10)
    d.rectangle([0, H - 10, W, H], fill=rgb(accent))   # 아래 강조선
    return img.convert("RGB")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for theme in THEMES:
        for n in range(1, 7):
            cover(theme, n).save(OUT / f"{theme}-{n}.webp", "WEBP", quality=82)
    print(len(THEMES) * 6, "장")


if __name__ == "__main__":
    main()
