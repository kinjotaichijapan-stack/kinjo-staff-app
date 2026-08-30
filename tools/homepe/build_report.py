"""施設別サマリー表の生成。

各施設の宿帳くん管理画面から
  - 残室数 / 予約数（予約・残室状況）
  - 客単価（統計・分析(期間)）
を取得し、任意で「受付分」PDF由来のデータ（受付泊数・売上・単価）を
突き合わせて、料金の上げ下げ判断用サマリーを出力する。

出力:
  summary.csv / summary.md      施設別サマリー（1行1施設）
  remaining_detail.csv          施設×日付の残室・予約（ロング形式）

認証情報CSV（既定 credentials.csv, .gitignore 済）の列:
  ログインID, パスワード, ホテル名, エリア  （余分な列は無視）
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from datetime import date, datetime, timedelta

import homepe_client as hc


ROMAN = {"Ⅰ": "i", "Ⅱ": "ii", "Ⅲ": "iii", "Ⅳ": "iv", "Ⅴ": "v",
         "Ⅵ": "vi", "Ⅶ": "vii", "Ⅷ": "viii", "Ⅸ": "ix", "Ⅹ": "x"}

# ログインID -> 受付分CSVの施設名（正規化で一致しない少数の手当て）
NAME_OVERRIDE = {
    "premiumterrace": "Mr.KINJO in CHATAN Premium Terrace",
    "nicas": "Mr.KINJO in Nica's牧志駅",
}


def norm(s: str) -> str:
    s = (s or "")
    for k, v in ROMAN.items():
        s = s.replace(k, v)
    s = s.lower()
    s = s.replace("’", "").replace("'", "").replace("　", " ")
    for junk in ["mr.kinjo", "mr kinjo", "link", "colorz", "color z", "ange miona",
                 "oceanterrace", "ocean terrace", "resort"]:
        s = s.replace(junk, " ")
    s = re.sub(r"\b(in|inn|the)\b", " ", s)
    s = re.sub(r"[^0-9a-z\u3040-\u30ff\u4e00-\u9fff]", "", s)
    return s


def load_credentials(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        rr = {k.strip(): (v.strip() if v else "") for k, v in r.items()}
        idc = rr.get("ログインID") or rr.get("id") or rr.get("idcode")
        pw = rr.get("パスワード") or rr.get("password") or rr.get("passwd")
        if not idc or not pw:
            continue
        out.append(
            {
                "id": idc,
                "pw": pw,
                "name": rr.get("ホテル名") or rr.get("name") or idc,
                "area": rr.get("エリア") or rr.get("area") or "",
            }
        )
    return out


def load_received(path: str) -> dict[str, dict]:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        return {}
    return {norm(r["name"]): r for r in rows}


def scrape_facility(fac: dict, start: date, days: int, price_days: int) -> dict:
    s = hc.HomepeSession()
    s.login(fac["id"], fac["pw"])
    wk = s.fetch_remaining_range(start, days)

    price_end = start + timedelta(days=price_days - 1)
    stats = s.fetch_rsvgraph(start, price_end)
    tot_sales = sum(c.sales for c in stats)
    tot_guests = sum(c.guests for c in stats)
    unit_price = round(tot_sales / tot_guests) if tot_guests else 0

    total_rooms = wk.total_rooms
    all_days = sorted(wk.remaining)
    cap = total_rooms * len(all_days)
    booked_rn = sum(wk.booked.values())
    remaining_rn = sum(wk.remaining.values())
    occ = booked_rn / cap * 100 if cap else 0
    sold_out = sum(1 for d in all_days if wk.remaining[d] <= 0)

    next7 = [d for d in all_days if d <= (start + timedelta(days=6)).strftime("%Y/%m/%d")]
    rem7 = sum(wk.remaining[d] for d in next7)
    cap7 = total_rooms * len(next7)
    occ7 = (cap7 - rem7) / cap7 * 100 if cap7 else 0

    return {
        "total_rooms": total_rooms,
        "occ": occ,
        "remaining_rn": remaining_rn,
        "booked_rn": booked_rn,
        "sold_out_days": sold_out,
        "occ7": occ7,
        "rem7": rem7,
        "unit_price": unit_price,
        "price_period": getattr(s, "last_rsvgraph_period", ""),
        "by_day": {d: (wk.remaining[d], wk.booked.get(d, 0)) for d in all_days},
        "room_types": [(rt.name, rt.total) for rt in wk.room_types],
    }


def price_hint(occ: float, occ7: float, unit: int, recv_unit: int) -> str:
    """料金の上げ下げの目安（簡易）。近接の稼働が高く単価が低いなら上げ、
    稼働が低いなら下げ、それ以外は維持。"""
    if occ7 >= 85 or occ >= 80:
        return "上げ↑"
    if occ7 <= 35 and occ <= 45:
        return "下げ↓"
    return "維持→"


def build(args) -> None:
    creds = load_credentials(args.credentials)
    if args.limit:
        creds = creds[: args.limit]
    if args.only:
        keep = {x.strip() for x in args.only.split(",")}
        creds = [c for c in creds if c["id"] in keep]
    received = load_received(args.received)

    start = (
        datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else date.today()
    )

    summary_rows = []
    detail_rows = []
    for i, fac in enumerate(creds, 1):
        label = f"[{i}/{len(creds)}] {fac['id']} {fac['name']}"
        try:
            m = scrape_facility(fac, start, args.days, args.price_days)
        except hc.LoginError as e:
            print(f"{label}: ログイン失敗 ({e})", file=sys.stderr)
            summary_rows.append({**_empty_row(fac), "status": "ログイン失敗"})
            continue
        except Exception as e:  # noqa: BLE001
            print(f"{label}: エラー ({e})", file=sys.stderr)
            summary_rows.append({**_empty_row(fac), "status": f"エラー:{e}"})
            continue

        rec = received.get(norm(fac["name"]), {})
        if not rec and fac["id"] in NAME_OVERRIDE:
            rec = received.get(norm(NAME_OVERRIDE[fac["id"]]), {})
        recv_unit = int(rec.get("unit", 0) or 0)
        row = {
            "no": i,
            "id": fac["id"],
            "施設名": fac["name"],
            "エリア": fac["area"],
            "ランク": rec.get("rank", ""),
            "総室数": m["total_rooms"],
            f"稼働率{args.days}日%": round(m["occ"], 1),
            f"残室{args.days}日(室泊)": m["remaining_rn"],
            "直近7日稼働%": round(m["occ7"], 1),
            "直近7日残室": m["rem7"],
            f"満室日数/{args.days}": m["sold_out_days"],
            "予約単価(客単価)": m["unit_price"],
            "受付泊数": rec.get("nights", ""),
            "受付売上": rec.get("sales", ""),
            "受付単価": rec.get("unit", ""),
            "料金目安": price_hint(m["occ"], m["occ7"], m["unit_price"], recv_unit),
            "status": "OK",
        }
        summary_rows.append(row)
        for d, (rem, bk) in m["by_day"].items():
            detail_rows.append(
                {"id": fac["id"], "施設名": fac["name"], "日付": d,
                 "残室": rem, "予約": bk, "総室数": m["total_rooms"]}
            )
        print(f"{label}: OK 稼働{row[f'稼働率{args.days}日%']}% 単価{m['unit_price']:,}")
        time.sleep(args.sleep)

    _write_summary_csv(args.out_csv, summary_rows, args.days)
    _write_summary_md(args.out_md, summary_rows, args.days, start)
    _write_detail_csv(args.out_detail, detail_rows)
    print(
        f"\n完了: {sum(1 for r in summary_rows if r.get('status')=='OK')}/"
        f"{len(summary_rows)} 施設。 -> {args.out_csv}, {args.out_md}, {args.out_detail}"
    )


def _empty_row(fac: dict) -> dict:
    return {"id": fac["id"], "施設名": fac["name"], "エリア": fac["area"]}


def _summary_fields(days: int) -> list[str]:
    return [
        "no", "id", "施設名", "エリア", "ランク", "総室数",
        f"稼働率{days}日%", f"残室{days}日(室泊)", "直近7日稼働%", "直近7日残室",
        f"満室日数/{days}", "予約単価(客単価)", "受付泊数", "受付売上", "受付単価",
        "料金目安", "status",
    ]


def _write_summary_csv(path: str, rows: list[dict], days: int) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_summary_fields(days))
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in _summary_fields(days)})


def _write_detail_csv(path: str, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["id", "施設名", "日付", "残室", "予約", "総室数"])
        w.writeheader()
        w.writerows(rows)


def _write_summary_md(path: str, rows: list[dict], days: int, start: date) -> None:
    ok = [r for r in rows if r.get("status") == "OK"]
    cols = ["施設名", "エリア", "ランク", "総室数", f"稼働率{days}日%",
            "直近7日稼働%", "直近7日残室", f"満室日数/{days}", "予約単価(客単価)",
            "受付泊数", "受付単価", "料金目安"]
    lines = [
        f"# Mr.KINJO 施設別 残室・単価サマリー",
        "",
        f"- 基準日: {start:%Y-%m-%d}（今後{days}日を集計）",
        f"- 予約単価＝客単価（売上÷延人数、宿泊予定日ベース）／受付単価＝受付分売上÷泊数",
        f"- 料金目安: 直近7日稼働≥85%または{days}日稼働≥80%→上げ、"
        f"直近7日稼働≤35%かつ{days}日稼働≤45%→下げ、その他→維持",
        "",
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for r in ok:
        vals = []
        for c in cols:
            v = r.get(c, "")
            if c in ("受付売上",) and v:
                v = f"{int(v):,}"
            if c in ("予約単価(客単価)",) and v:
                v = f"{int(v):,}"
            vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    failed = [r for r in rows if r.get("status") != "OK"]
    if failed:
        lines += ["", "## 取得失敗", ""]
        for r in failed:
            lines.append(f"- {r.get('施設名')} ({r.get('id')}): {r.get('status')}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="宿帳くん 残室・単価サマリー生成")
    ap.add_argument("-c", "--credentials", default="credentials.csv")
    ap.add_argument("-r", "--received", default="received.csv",
                    help="受付分PDF由来CSV（無ければスキップ）")
    ap.add_argument("--start", default="", help="集計開始日 YYYY-MM-DD（既定=今日）")
    ap.add_argument("--days", type=int, default=30, help="残室集計日数")
    ap.add_argument("--price-days", type=int, default=60, help="客単価集計日数")
    ap.add_argument("--sleep", type=float, default=1.0, help="施設間の待機秒")
    ap.add_argument("--limit", type=int, default=0, help="先頭N施設のみ")
    ap.add_argument("--only", default="", help="対象ログインIDをカンマ区切りで限定")
    ap.add_argument("--out-csv", default="summary.csv")
    ap.add_argument("--out-md", default="summary.md")
    ap.add_argument("--out-detail", default="remaining_detail.csv")
    args = ap.parse_args()
    build(args)


if __name__ == "__main__":
    main()
