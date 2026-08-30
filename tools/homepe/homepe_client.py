"""宿帳くんフロンティア（homepe.net）ユーザー管理画面のログイン／データ取得クライアント。

料金の上げ下げ判断に必要な「残室数」「予約数」「単価（客単価）」を
各施設の管理画面から取得することを目的とする。

取得元画面:
  - ydf_reserveweek.html  予約・残室状況（部屋タイプ別・日別の残室数／予約数）
  - ydf_rsvgraph.html     統計・分析(期間)（申込経路別の売上・客単価）

認証情報はコードに埋め込まず、CSV（ログインID／パスワード）から読み込む。
"""

from __future__ import annotations

import http.cookiejar
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta

BASE = "https://www.homepe.net/"
ENC = "shift_jis"

# ログイン後の中継フォームやメニューフォームで引き回される認証系フィールド。
# passex は1リクエストごとに更新されるローリングトークンのため、
# 常に直近レスポンスから取り直す必要がある。
AUTH_FIELDS = (
    "idcode",
    "passex",
    "acount",
    "domain",
    "u_name",
    "acdate",
    "nmflag",
    "ret",
    "current_dir",
)


class LoginError(RuntimeError):
    """ログイン失敗（ID/パスワード誤り、施設無効など）。"""


@dataclass
class RoomType:
    number: str
    name: str
    total: int


@dataclass
class ReserveWeek:
    """予約・残室状況の取得結果。

    remaining[date] / booked[date] は全部屋タイプ合計の日別値。
    by_type[room_number][date] に部屋タイプ別の (残室, 予約) を保持する。
    """

    room_types: list[RoomType] = field(default_factory=list)
    remaining: dict[str, int] = field(default_factory=dict)
    booked: dict[str, int] = field(default_factory=dict)
    by_type: dict[str, dict[str, tuple[int, int]]] = field(default_factory=dict)

    @property
    def total_rooms(self) -> int:
        return sum(rt.total for rt in self.room_types)


@dataclass
class ChannelStat:
    channel: str
    reservations: int
    guests: int
    sales: int
    lodging: int
    option: int
    unit_price: int  # 客単価
    cancels: int


class HomepeSession:
    def __init__(self, timeout: int = 40, retries: int = 3):
        self.timeout = timeout
        self.retries = retries
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj)
        )
        self.opener.addheaders = [("User-Agent", "Mozilla/5.0 (kinjo-staff-app report)")]
        self.last_html: str = ""

    # ---- low level -------------------------------------------------------
    def _post(self, url: str, pairs) -> str:
        body = "&".join(
            f"{urllib.parse.quote(k)}={urllib.parse.quote(str(v).encode(ENC, 'replace'))}"
            for k, v in pairs
        ).encode()
        last_err = None
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                resp = self.opener.open(req, timeout=self.timeout)
                return resp.read().decode(ENC, "replace")
            except Exception as e:  # noqa: BLE001 - network retry
                last_err = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"POST failed for {url}: {last_err}")

    def _auth_from(self, html: str) -> dict[str, str]:
        auth: dict[str, str] = {}
        for name in AUTH_FIELDS:
            m = re.search(
                rf"name=['\"]{name}['\"][^>]*?value=['\"]([^'\"]*)['\"]", html
            )
            if m:
                auth[name] = m.group(1)
        return auth

    # ---- flow ------------------------------------------------------------
    def login(self, idcode: str, passwd: str) -> None:
        relay = self._post(
            BASE + "ydf_login.html",
            [
                ("sbutton", "login"),
                ("setstring", "[ 宿泊予約管理 ＆ 各サイトへの手動同期 ] "),
                ("goscript", "ydf_top.html"),
                ("system", "ydf"),
                ("idcode", idcode),
                ("passwd", passwd),
            ],
        )
        # ログイン成功時は ydf_top.html への自己送信フォームが返る。
        # 失敗時はログインフォームが再表示される。
        if "name='form1'" not in relay and 'name="form1"' not in relay:
            if "passwd" in relay and "idcode" in relay:
                raise LoginError(f"login rejected for {idcode}")
            raise LoginError(f"unexpected login response for {idcode}")
        fields = dict(re.findall(r"name='([^']+)'\s+value='([^']*)'", relay))
        m = re.search(r"action='([^']+)'", relay)
        action = m.group(1) if m else BASE + "ydf_top.html"
        top = self._post(action, list(fields.items()))
        self.last_html = top
        self._top_auth = self._auth_from(top)
        # メニューフォーム（RsvMenuForm）が無ければログイン後画面ではない。
        if "RsvMenuForm" not in top:
            raise LoginError(f"post-login menu not found for {idcode}")

    def _menu_fields(self, html: str | None = None) -> dict[str, str]:
        html = html or self.last_html
        m = re.search(r"<form name='RsvMenuForm'.*?</form>", html, re.S)
        fields: dict[str, str] = {}
        block = m.group(0) if m else html
        for im in re.finditer(r"<input[^>]*name='([^']+)'[^>]*>", block):
            tag, name = im.group(0), im.group(1)
            vm = re.search(r"value='([^']*)'", tag)
            fields[name] = vm.group(1) if vm else ""
        return fields

    def goto(self, action: str, extra: dict | None = None) -> str:
        fields = self._menu_fields()
        if extra:
            fields.update(extra)
        html = self._post(BASE + action, list(fields.items()))
        self.last_html = html
        return html

    # ---- reserve week (remaining rooms) ---------------------------------
    def fetch_reserve_week(self, start: date) -> ReserveWeek:
        """start から始まる約15日分の残室状況を取得。"""
        html = self.goto(
            "ydf_reserveweek.html", {"staday": start.strftime("%Y/%m/%d")}
        )
        return self._parse_reserve_week(html)

    @staticmethod
    def _parse_reserve_week(html: str) -> ReserveWeek:
        result = ReserveWeek()
        # 残数(rz) と 予約数(ry) セル（部屋タイプ別・日別の合計行）
        rz = re.findall(r"id='rz(\d+)_([\d\-]+)'[^>]*>\s*([\-\d]*)\s*<", html)
        ry = re.findall(r"id='ry(\d+)_([\d\-]+)'[^>]*>\s*([\-\d]*)\s*<", html)

        # 部屋タイプ番号 -> (名称, 総室数)
        # 見出し: class='HeyaBtnN'>名称 ...【..平米】 (総室数)<br>
        type_totals: dict[str, tuple[str, int]] = {}
        for m in re.finditer(
            r"class='HeyaBtn(\d+)'>\s*([^<]+?)\s*\((\d+)\)\s*<br>", html
        ):
            type_totals[m.group(1)] = (m.group(2).strip(), int(m.group(3)))

        def to_int(v: str) -> int:
            v = v.strip()
            return int(v) if v.lstrip("-").isdigit() else 0

        for tno, d, v in rz:
            iso = d.replace("-", "/")
            result.by_type.setdefault(tno, {})
            prev = result.by_type[tno].get(iso, (0, 0))
            result.by_type[tno][iso] = (to_int(v), prev[1])
            result.remaining[iso] = result.remaining.get(iso, 0) + to_int(v)
        for tno, d, v in ry:
            iso = d.replace("-", "/")
            result.by_type.setdefault(tno, {})
            prev = result.by_type[tno].get(iso, (0, 0))
            result.by_type[tno][iso] = (prev[0], to_int(v))
            result.booked[iso] = result.booked.get(iso, 0) + to_int(v)

        for tno in sorted(result.by_type, key=int):
            name, total = type_totals.get(tno, ("", 0))
            result.room_types.append(RoomType(number=tno, name=name, total=total))
        return result

    def fetch_remaining_range(self, start: date, days: int) -> ReserveWeek:
        """start から days 日分を、15日ごとにページ送りして結合取得。"""
        combined = ReserveWeek()
        seen_types = False
        cursor = start
        end = start + timedelta(days=days)
        while cursor < end:
            wk = self.fetch_reserve_week(cursor)
            if not seen_types and wk.room_types:
                combined.room_types = wk.room_types
                seen_types = True
            for iso, val in wk.remaining.items():
                combined.remaining[iso] = val
            for iso, val in wk.booked.items():
                combined.booked[iso] = val
            for tno, days_map in wk.by_type.items():
                combined.by_type.setdefault(tno, {}).update(days_map)
            cursor += timedelta(days=15)
            time.sleep(0.3)
        # 範囲外の日付を除去
        keep = {
            (start + timedelta(days=i)).strftime("%Y/%m/%d") for i in range(days)
        }
        combined.remaining = {k: v for k, v in combined.remaining.items() if k in keep}
        combined.booked = {k: v for k, v in combined.booked.items() if k in keep}
        return combined

    # ---- zaiko calendar (authoritative inventory) -----------------------
    def _fetch_zaiko_page(self, rtypno: str, sitecd: str, cln_base: int) -> str:
        fields = self._menu_fields()
        fields.update(
            {"rtypno": str(rtypno), "sitecd": str(sitecd), "cln_base": str(cln_base)}
        )
        html = self._post(BASE + "ydf_zaikocalendar.html", list(fields.items()))
        self.last_html = html
        return html

    @staticmethod
    def _zaiko_room_types(html: str) -> list[tuple[str, str]]:
        m = re.search(r"name='rtypno'.*?</select>", html, re.S)
        if not m:
            return []
        return re.findall(r"<option value='(\d+)'[^>]*>([^<]+)</option>", m.group(0))

    @staticmethod
    def _zaiko_max_rooms(html: str) -> int:
        m = re.search(r"最大部屋提供数：(\d+)", html)
        return int(m.group(1)) if m else 0

    @staticmethod
    def _zaiko_remaining(html: str, rtypno: str, sitecd: str) -> dict[str, int]:
        # 残数(rtpz) は共通在庫のためどのサイトでも同値。sitecd=1 を基準に読む。
        out: dict[str, int] = {}
        for d, v in re.findall(
            rf"id='rtpz\[{rtypno}\]\[{sitecd}\]\[([\d/]+)\]'>\s*(-?\d+)\s*<", html
        ):
            out[d] = int(v)
        return out

    def fetch_zaiko_inventory(self, start: date, days: int, base_today: date | None = None) -> ReserveWeek:
        """在庫カレンダーから残室（＝共通在庫の残数）を取得して集計する。

        残数は共通在庫のため sitecd=1（宿帳くん）を基準に部屋タイプ別へ読み、
        総室数は各部屋タイプの「最大部屋提供数」で求める。
        予約数 = 総室数 − 残数（日別・部屋タイプ合計）。
        """
        today = base_today or date.today()
        end = start + timedelta(days=days)
        # cln_base は当月=0 の月オフセット
        def month_offset(d: date) -> int:
            return (d.year - today.year) * 12 + (d.month - today.month)

        months = sorted(
            {month_offset(start + timedelta(days=i)) for i in range(days)}
        )

        result = ReserveWeek()
        # 部屋タイプ一覧を最初の1回で取得
        first = self._fetch_zaiko_page("1", "1", months[0])
        room_types = self._zaiko_room_types(first)
        if not room_types:
            room_types = [("1", "")]

        totals: dict[str, int] = {}
        rem_by_type: dict[str, dict[str, int]] = {}
        for rtypno, name in room_types:
            rem_by_type.setdefault(rtypno, {})
            for i, cln in enumerate(months):
                html = first if (rtypno == "1" and i == 0 and cln == months[0]) else \
                    self._fetch_zaiko_page(rtypno, "1", cln)
                totals[rtypno] = max(totals.get(rtypno, 0), self._zaiko_max_rooms(html))
                rem_by_type[rtypno].update(self._zaiko_remaining(html, rtypno, "1"))
                time.sleep(0.2)
            result.room_types.append(
                RoomType(number=rtypno, name=name.strip(), total=totals.get(rtypno, 0))
            )

        keep = {
            (start + timedelta(days=i)).strftime("%Y/%m/%d") for i in range(days)
        }
        for rtypno, days_map in rem_by_type.items():
            cap = totals.get(rtypno, 0)
            for iso, rem in days_map.items():
                if iso not in keep:
                    continue
                result.by_type.setdefault(rtypno, {})[iso] = (rem, max(cap - rem, 0))
                result.remaining[iso] = result.remaining.get(iso, 0) + rem
                booked = max(cap - rem, 0)
                result.booked[iso] = result.booked.get(iso, 0) + booked
        return result

    # ---- rsvgraph (unit price by channel) -------------------------------
    def fetch_rsvgraph(self, start: date, end: date) -> list[ChannelStat]:
        html = self.goto(
            "ydf_rsvgraph.html",
            {
                "staday": start.strftime("%Y/%m/%d"),
                "endday": end.strftime("%Y/%m/%d"),
            },
        )
        self.last_rsvgraph_period = self._rsvgraph_period(html)
        return self._parse_rsvgraph(html)

    @staticmethod
    def _rsvgraph_period(html: str) -> str:
        m = re.search(r"集計期間(\d{4}/\d{2}/\d{2})〜(\d{4}/\d{2}/\d{2})", html)
        return f"{m.group(1)}〜{m.group(2)}" if m else ""

    @staticmethod
    def _parse_rsvgraph(html: str) -> list[ChannelStat]:
        stats: list[ChannelStat] = []
        # 申込経路別 予約集計テーブルを対象にする
        i = html.find("申込経路別")
        if i < 0:
            return stats
        segment = html[i : i + 20000]
        rows = re.split(r"</tr>", segment)
        for row in rows:
            cells = [
                re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
                for c in re.split(r"</td>", row)
            ]
            cells = [c for c in cells if c != ""]
            if len(cells) < 7:
                continue
            name = cells[0]
            nums = []
            for c in cells[1:]:
                cc = c.replace(",", "").replace("¥", "")
                cc = re.sub(r"\(.*?\)", "", cc).replace("%", "").strip()
                if re.fullmatch(r"-?\d+", cc):
                    nums.append(int(cc))
                else:
                    nums.append(None)
            if name in ("申込経路", "合計") or not name:
                continue
            if sum(1 for n in nums if n is not None) < 4:
                continue
            def g(idx):
                return nums[idx] if idx < len(nums) and nums[idx] is not None else 0
            stats.append(
                ChannelStat(
                    channel=name,
                    reservations=g(0),
                    guests=g(1),
                    sales=g(2),
                    lodging=g(3),
                    option=g(4),
                    unit_price=g(5),
                    cancels=g(6),
                )
            )
        return stats
