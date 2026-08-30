# 宿帳くん 残室・単価サマリー ツール

Mr.KINJO 各施設の宿帳くんフロンティア管理画面（`homepe.net`）へ自動ログインし、
料金の上げ下げ判断に使う **残室数** と **予約単価（客単価）** を取得して、
施設別サマリー表（CSV / Markdown）を生成する。

任意で「宿泊数集計」PDF（受付分レポート）を読み込み、**受付泊数・受付売上・受付単価**
（入った予約の1泊あたり単価）を各施設に突き合わせる。

## 取得元の画面

| 画面 | URL | 取得する値 |
| --- | --- | --- |
| 予約・残室状況 | `ydf_reserveweek.html` | 部屋タイプ別・日別の残室数／予約数 |
| 統計・分析(期間) | `ydf_rsvgraph.html` | 申込経路別の売上・客単価 |

## 構成

- `homepe_client.py` — ログインとデータ取得のクライアント（標準ライブラリのみ）
- `parse_summary_pdf.py` — 「宿泊数集計」PDF → `received.csv`
- `build_report.py` — 全施設をスクレイピングしてサマリーを生成

## 使い方

1. 認証情報CSVを用意（`credentials.example.csv` を参照）。ファイル名は `credentials.csv`。
   このファイルは `.gitignore` 済みでコミットされない。

   ```
   ログインID,パスワード,ホテル名,エリア
   eminencemakishi,xxxx,Mr.KINJO Eminence inn Makishi,那覇
   ...
   ```

2. （任意）受付分PDFを解析:

   ```bash
   pip install -r requirements.txt
   python parse_summary_pdf.py 8月27日受付分.pdf -o received.csv
   ```

3. サマリー生成:

   ```bash
   python build_report.py --days 30 --price-days 60 --start 2026-08-30
   # 一部施設だけ試す: --limit 3  /  --only eminencemakishi,violette
   ```

## 出力

- `summary.csv` / `summary.md` — 施設別サマリー（1行1施設）
- `remaining_detail.csv` — 施設×日付の残室・予約（ロング形式。ピボット用）

### サマリーの列

| 列 | 意味 |
| --- | --- |
| 総室数 | 全部屋タイプの合計室数 |
| 稼働率N日% | 今後N日の予約室泊 ÷ 総容量 |
| 直近7日稼働% / 残室 | 直近7日の稼働率・残室（室泊） |
| 満室日数/N | 今後N日のうち残室0の日数 |
| 予約単価(客単価) | 売上÷延人数（宿泊予定日ベース、`--price-days` 期間） |
| 受付泊数 / 受付売上 / 受付単価 | 受付分PDF由来（受付単価＝売上÷泊数） |
| 料金目安 | 稼働から算出した上げ／下げ／維持の目安（下記） |

**料金目安のロジック（簡易・目安）**: 直近7日稼働≥85% または N日稼働≥80% → 上げ、
直近7日稼働≤35% かつ N日稼働≤45% → 下げ、その他 → 維持。
実際の判断は料金表のルール（曜日・季節・イベント・離島係数など）と併せて行うこと。

## 注意

- `credentials.csv` は平文パスワードを含む。共有・コミット厳禁。
- 本番の予約システムへ連続アクセスするため、`--sleep` で施設間の待機を確保している。
