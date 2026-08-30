"""「宿泊数集計」PDF（受付分レポート）から施設別の受付実績を抽出する。

出力CSV: no, name, rank, nights(受付泊数), sales(受付売上), unit(受付単価=売上÷泊数)

受付単価は「入った予約の1泊あたり単価」で、料金の上げ下げ判断に使う指標。
"""

from __future__ import annotations

import argparse
import csv
import re
import sys


def parse(pdf_path: str) -> list[dict]:
    try:
        import pymupdf  # noqa: F401
    except ImportError:  # pragma: no cover
        print("pymupdf が必要です: pip install pymupdf", file=sys.stderr)
        raise

    import pymupdf

    doc = pymupdf.open(pdf_path)
    text = "\n".join(page.get_text() for page in doc)

    # 詳細セクションの明快な見出しを利用:
    #   "1. Mr.KINJO in 旭橋駅前 — 合計 92泊 ／ 売上 ¥814,901"
    header = re.findall(
        r"^(\d+)\.\s+(.+?)\s+—\s+合計\s+([\d,]+)泊\s*／\s*売上\s+¥([\d,]+)",
        text,
        re.M,
    )

    # ランクは施設別合計テーブル側にある。(泊数, 売上) をキーに対応付ける。
    rank_by_key = _parse_ranks(text)

    rows = []
    for no, name, nights, sales in header:
        name = re.sub(r"\s+", " ", name).strip()
        n = int(nights.replace(",", ""))
        s = int(sales.replace(",", ""))
        unit = round(s / n) if n else 0
        rows.append(
            {
                "no": int(no),
                "name": name,
                "rank": rank_by_key.get((n, s), ""),
                "nights": n,
                "sales": s,
                "unit": unit,
            }
        )
    return rows


def _parse_ranks(text: str) -> dict[tuple[int, int], str]:
    """施設別合計テーブルから (泊数, 売上)->ランク を得る。

    テーブル行はおおむね
        No / 施設名(複数行) / ランク / 泊数 / ¥売上
    の順。¥売上をアンカーに直前2整数（泊数・ランク）を拾う。
    """
    ranks: dict[tuple[int, int], str] = {}
    head = text.split("施設別 予約一覧", 1)[0]  # 先頭の合計テーブル領域のみ
    tokens = [t.strip() for t in head.splitlines() if t.strip()]
    for i, tok in enumerate(tokens):
        m = re.fullmatch(r"¥([\d,]+)", tok)
        if not m:
            continue
        sales = int(m.group(1).replace(",", ""))
        # 直前: 泊数, その前: ランク
        prev = [t for t in tokens[max(0, i - 4):i]]
        ints = [p for p in prev if re.fullmatch(r"[\d,]+", p)]
        if len(ints) >= 2:
            nights = int(ints[-1].replace(",", ""))
            rank = ints[-2]
            if rank in ("1", "2"):
                ranks[(nights, sales)] = rank
    return ranks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", default="received.csv")
    args = ap.parse_args()
    rows = parse(args.pdf)
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["no", "name", "rank", "nights", "sales", "unit"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} 施設を {args.out} に書き出しました")


if __name__ == "__main__":
    main()
