"""네이버 검색광고 키워드도구로 업종별 실제 검색량을 받아 keywords/volume_<업종>.csv 에 쓴다(DB 는 건드리지 않는다).

시드: ① 업종 주제 용어 ② 거기서 나온 연관 키워드 중 업종 낱말이 든 상위 60개(질문형 발굴) ③ 시도·시군구 × 대표 주제 2개.
연관 키워드는 업종 낱말(STEM)이 든 것만 남긴다. 다시 돌리면 이미 받은 시드는 건너뛴다(keywords/volume_seen.txt).

  python scripts/kw_volume.py            # 전 업종
  python scripts/kw_volume.py 음주운전    # 한 업종
"""
import csv
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import create_app  # noqa: E402
import db  # noqa: E402
import keywords  # noqa: E402
from db import ops  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "keywords"
STEM = {"개인회생": "회생 파산 면책 채무 빚 변제 압류 추심 신용회복 워크아웃 탕감", "음주운전": "음주 면허 측정 숙취 윤창호",
        "태아보험": "태아 임신 임산부 어린이보험 산모 출산", "장기렌트": "렌트 리스 렌터카", "중고차": "중고",
        "철거": "철거 원상복구 폐업", "배관": "막힘 누수 하수구 변기 싱크대 배관 해빙 역류 배수구",
        "이사": "이사 이삿짐 용달", "입주청소": "청소"}


def fetch(hints):
    for wait in (0, 2, 5, 15, 40):
        time.sleep(0.4 + wait)
        try:
            return keywords.fetch_naver(hints)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503):
                print("  건너뜀", e.code, hints, flush=True)
                return []
        except OSError:
            pass
    return []


def main(only=None):
    seen_f = OUT / "volume_seen.txt"
    seen = set(seen_f.read_text("utf-8").split("\n")) if seen_f.exists() else set()
    regions = [r["short_name"] for r in db.q("SELECT short_name FROM regions WHERE level IN (1,2) ORDER BY level, sort_order")]
    for v in db.q("SELECT v.id, v.slug FROM verticals v WHERE v.site_id > 1 ORDER BY v.id"):
        if only and v["slug"] != only:
            continue
        stems = STEM[v["slug"]].split()
        path = OUT / f"volume_{v['slug']}.csv"
        rows = {}
        if path.exists():
            rows = {r["keyword"]: r for r in csv.DictReader(path.open(encoding="utf-8-sig"))}

        def run(seeds, kind):
            seeds = [s for s in dict.fromkeys(seeds) if f"{v['slug']}|{keywords.norm(s)}" not in seen]
            for i in range(0, len(seeds), 5):
                part = seeds[i:i + 5]
                want = {keywords.norm(s) for s in part}
                for r in fetch(part):
                    k = r["keyword"]
                    if k in want or any(s in k for s in stems):
                        rows[k] = {"keyword": k, "pc": r["pc"], "mobile": r["mobile"], "total": r["pc"] + r["mobile"],
                                   "competition": r["competition"], "seed": kind if k in want else rows.get(k, {}).get("seed") or "related"}
                seen.update(f"{v['slug']}|{keywords.norm(s)}" for s in part)
                if i % 50 == 0:
                    print(f"[{v['slug']}] {kind} {i}/{len(seeds)} · 키워드 {len(rows)}", flush=True)
                    save()

        def save():
            with path.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, ["keyword", "pc", "mobile", "total", "competition", "seed"])
                w.writeheader()
                w.writerows(sorted(rows.values(), key=lambda r: -int(r["total"])))
            seen_f.write_text("\n".join(sorted(seen)), "utf-8")

        topics = [t["term"] for t in ops.terms(v["id"]) if t["dim"] == "topic"]
        run(topics, "topic")
        top = sorted((r for r in rows.values() if any(s in r["keyword"] for s in stems)), key=lambda r: -int(r["total"]))[:60]
        run([r["keyword"] for r in top], "expand")
        run([f"{reg}{t}" for t in topics[:2] for reg in regions], "region")
        save()
        print(f"[{v['slug']}] 끝 · 키워드 {len(rows)} → {path.name}", flush=True)


if __name__ == "__main__":
    with create_app().app_context():
        main(sys.argv[1] if len(sys.argv) > 1 else None)
