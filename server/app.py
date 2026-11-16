"""服刑日誌的後端：收網頁結算後上傳的修練紀錄，存進 SQLite，算排行榜。

只用標準函式庫，放在記憶體與空間都很小的機器（Raspberry Pi 3）上跑：不用裝套件，
資料就是一個 SQLite 檔，備份時複製那個檔就好。

沒有登入也沒有帳號：一筆紀錄靠網頁產生的隨機 uid 辨認，同一筆重傳不會變兩筆，
知道 uid 的人（也就是存著那筆紀錄的瀏覽器）才能刪掉它。uid 與上傳者的 IP 只存不對外，
排行榜的回應裡不會出現。

數字是瀏覽器自己算好傳上來的，這裡只擋格式不對與明顯不可能的值，擋不了有心造假。

路徑：
    POST   /api/records        {"records": [...]}，回 {"accepted": [uid], "rejected": [{uid, reason}]}
    DELETE /api/records/<uid>  刪一筆，有沒有那筆都回 204
    GET    /api/leaderboard    ?days=7&limit=50，回三個榜（總經驗、最高效率、等級）
    GET    /api/health         {"ok": true, "records": 筆數}
    GET    /                   直接看排行榜的簡單頁面
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import re
import shutil
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

MAX_BODY = 128 * 1024            # 一次請求的上限；一批 100 筆大約 30 KB
MAX_BATCH = 100
MAX_DB_BYTES = 200 * 1024 * 1024  # 資料檔長到這麼大就不再收，免得把 SD 卡塞滿
MIN_FREE_BYTES = 100 * 1024 * 1024
RATE_LIMIT = 120                  # 每個 IP 每分鐘幾個請求
RATE_WINDOW = 60.0
RATE_MIN_SEC = 600                # 效率榜只算活躍滿 10 分鐘的紀錄，免得打幾隻怪就衝上榜
BOARD_TTL = 60.0
MAX_LIMIT = 200

UID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
EARLIEST_MS = 1_577_836_800_000   # 2020-01-01，比這更早的時間一定是壞資料
DAY_MS = 86_400_000
MAX_SEC = 30 * 86_400             # 一段紀錄不可能超過 30 天
MAX_EXP = 10 ** 13

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    uid         TEXT PRIMARY KEY,
    character   TEXT NOT NULL,
    job         TEXT NOT NULL,
    level       INTEGER,
    map_id      TEXT NOT NULL,
    map_name    TEXT NOT NULL,
    start_at    INTEGER NOT NULL,
    end_at      INTEGER NOT NULL,
    cum_net     INTEGER NOT NULL,
    active_sec  REAL NOT NULL,
    idle_sec    REAL NOT NULL,
    ip          TEXT NOT NULL,
    received_at INTEGER NOT NULL
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS records_character ON records (character, end_at);
CREATE INDEX IF NOT EXISTS records_end ON records (end_at);
"""


class Invalid(ValueError):
    """上傳的紀錄不合格；訊息會回給網頁。"""


def _text(raw: dict, key: str, limit: int, *, required: bool = False) -> str:
    value = raw.get(key)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise Invalid(f"{key} 不是文字")
    value = value.strip()
    if len(value) > limit or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise Invalid(f"{key} 太長或有控制字元")
    if required and not value:
        raise Invalid(f"{key} 是空的")
    return value


def _number(raw: dict, key: str, low: float, high: float) -> float:
    value = raw.get(key)
    # bool 在 Python 裡也是 int，true 不該被當成 1
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Invalid(f"{key} 不是數字")
    if not low <= value <= high:
        raise Invalid(f"{key} 超出範圍")
    return value


def validate(raw: object, now_ms: int) -> dict:
    """把網頁的一筆紀錄（camelCase）檢查過，轉成資料表的欄位；不合格丟 Invalid。"""
    if not isinstance(raw, dict):
        raise Invalid("不是物件")
    uid = raw.get("uid")
    if not isinstance(uid, str) or not UID.match(uid):
        raise Invalid("uid 格式不對")
    level = raw.get("level")
    if level is not None:
        level = _number(raw, "level", 1, 300)
        if level != int(level):
            raise Invalid("level 不是整數")
        level = int(level)
    # 使用者的時鐘可能快一點，放寬一天
    start_at = int(_number(raw, "startAt", EARLIEST_MS, now_ms + DAY_MS))
    end_at = int(_number(raw, "endAt", start_at, now_ms + DAY_MS))
    cum_net = round(_number(raw, "cumNet", 0, MAX_EXP))
    if cum_net <= 0:
        raise Invalid("cumNet 不是正數")
    return {
        "uid": uid,
        "character": _text(raw, "character", 24, required=True),
        "job": _text(raw, "job", 24),
        "level": level,
        "map_id": _text(raw, "mapId", 128),
        "map_name": _text(raw, "mapName", 48),
        "start_at": start_at,
        "end_at": end_at,
        "cum_net": cum_net,
        "active_sec": float(_number(raw, "activeSec", 0, MAX_SEC)),
        "idle_sec": float(_number(raw, "idleSec", 0, MAX_SEC)),
    }


class Full(RuntimeError):
    """資料檔或磁碟滿了，不再收新紀錄。"""


class Store:
    """SQLite 的包裝。只有一條連線，所有讀寫排隊（量很小，不需要連線池）。"""

    def __init__(self, path: str | Path, *, max_bytes: int = MAX_DB_BYTES, min_free: int = MIN_FREE_BYTES) -> None:
        self.path = str(path)
        self.max_bytes = max_bytes
        self.min_free = min_free
        self._lock = threading.Lock()
        self._boards: dict[tuple, tuple[float, dict]] = {}
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        if self.path != ":memory:":
            # WAL + NORMAL：每次寫入少同步幾次磁碟，對 SD 卡比較溫和；斷電最多丟最後幾筆，不會壞檔
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _check_room(self) -> None:
        if self.path == ":memory:":
            return
        target = Path(self.path)
        if target.stat().st_size > self.max_bytes:
            raise Full("資料檔已達上限")
        if shutil.disk_usage(target.parent).free < self.min_free:
            raise Full("磁碟空間不足")

    def add(self, raws: list, ip: str, now_ms: int | None = None) -> tuple[list[str], list[dict]]:
        """存一批紀錄。回 (收下的 uid, 被拒絕的 {uid, reason})；已經有的 uid 也算收下。"""
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        accepted: list[str] = []
        rejected: list[dict] = []
        rows: list[dict] = []
        for raw in raws:
            try:
                row = validate(raw, now_ms)
            except Invalid as err:
                uid = raw.get("uid") if isinstance(raw, dict) else None
                rejected.append({"uid": uid if isinstance(uid, str) else None, "reason": str(err)})
                continue
            row["ip"] = ip
            row["received_at"] = now_ms
            rows.append(row)
            accepted.append(row["uid"])
        if rows:
            with self._lock:
                self._check_room()
                self._db.executemany(
                    "INSERT INTO records (uid, character, job, level, map_id, map_name, start_at, end_at,"
                    " cum_net, active_sec, idle_sec, ip, received_at)"
                    " VALUES (:uid, :character, :job, :level, :map_id, :map_name, :start_at, :end_at,"
                    " :cum_net, :active_sec, :idle_sec, :ip, :received_at)"
                    " ON CONFLICT (uid) DO NOTHING",
                    rows,
                )
                self._db.commit()
                self._boards.clear()
        return accepted, rejected

    def delete(self, uid: str) -> bool:
        with self._lock:
            done = self._db.execute("DELETE FROM records WHERE uid = ?", (uid,)).rowcount > 0
            self._db.commit()
            if done:
                self._boards.clear()
        return done

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM records").fetchone()[0]

    def boards(self, days: int | None = None, limit: int = 50, now_ms: int | None = None) -> dict:
        """三個榜，每個角色一列。days 給了就只算結束時間在最近這幾天內的紀錄。"""
        now = time.monotonic()
        key = (days, limit)
        with self._lock:
            hit = self._boards.get(key)
            if hit and now - hit[0] < BOARD_TTL and now_ms is None:
                return hit[1]
            now_ms = int(time.time() * 1000) if now_ms is None else now_ms
            since = now_ms - days * DAY_MS if days else 0
            # 職業取該角色最近一筆有讀到職業的（轉職後名字不變、職業會變）
            job = ("(SELECT job FROM records j WHERE j.character = r.character AND j.job <> ''"
                   " ORDER BY j.end_at DESC LIMIT 1)")
            exp = self._db.execute(
                f"SELECT character, {job}, MAX(level), SUM(cum_net) AS total, SUM(active_sec), COUNT(*)"
                " FROM records r WHERE end_at >= ? GROUP BY character ORDER BY total DESC, character LIMIT ?",
                (since, limit),
            ).fetchall()
            # 只有一個 MAX() 時，SQLite 保證沒聚合的欄位取自最大值那一列，所以等級、地圖是那一筆紀錄的
            rate = self._db.execute(
                "SELECT character, job, level, map_name, MAX(cum_net * 3600.0 / active_sec) AS best,"
                " active_sec, end_at"
                " FROM records r WHERE end_at >= ? AND active_sec >= ?"
                " GROUP BY character ORDER BY best DESC, character LIMIT ?",
                (since, RATE_MIN_SEC, limit),
            ).fetchall()
            level = self._db.execute(
                f"SELECT character, {job}, MAX(level) AS top, MIN(end_at)"
                " FROM records r WHERE end_at >= ? AND level IS NOT NULL"
                " GROUP BY character ORDER BY top DESC, character LIMIT ?",
                (since, limit),
            ).fetchall()
            out = {
                "days": days,
                "exp": [
                    {"character": c, "job": j or "", "level": lv, "exp": total, "activeSec": round(sec), "records": n}
                    for c, j, lv, total, sec, n in exp
                ],
                "rate": [
                    {"character": c, "job": j, "level": lv, "mapName": m, "rate": round(best),
                     "activeSec": round(sec), "endAt": end}
                    for c, j, lv, m, best, sec, end in rate
                ],
                "level": [{"character": c, "job": j or "", "level": top} for c, j, top, _ in level],
            }
            self._boards[key] = (now, out)
            return out


class RateLimiter:
    """每個 IP 每分鐘最多幾個請求（固定視窗）。只放在記憶體，重開就歸零。"""

    def __init__(self, limit: int = RATE_LIMIT, window: float = RATE_WINDOW) -> None:
        self.limit = limit
        self.window = window
        self._lock = threading.Lock()
        self._seen: dict[str, tuple[float, int]] = {}

    def allow(self, ip: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._seen) > 10_000:
                self._seen = {k: v for k, v in self._seen.items() if now - v[0] < self.window}
            start, count = self._seen.get(ip, (now, 0))
            if now - start >= self.window:
                start, count = now, 0
            self._seen[ip] = (start, count + 1)
            return count < self.limit


def client_ip(peer: str, headers) -> str:
    """上傳者的 IP。

    走 Cloudflare Tunnel 時連進來的是本機的 cloudflared，真正的來源在標頭裡；
    只有連線來自本機時才信標頭，直接連進來的人自己填的標頭不算數。
    """
    try:
        local = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        local = False
    if local:
        for name in ("CF-Connecting-IP", "X-Forwarded-For"):
            value = (headers.get(name) or "").split(",")[0].strip()
            try:
                return str(ipaddress.ip_address(value))
            except ValueError:
                continue
    return peer


PAGE = """<!doctype html>
<html lang="zh-Hant">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>服刑日誌排行榜</title>
<style>
  :root { color-scheme: light dark; --line: #8884; --dim: #888; }
  body { font: 15px/1.5 system-ui, "Noto Sans TC", sans-serif; margin: 0 auto; padding: 16px; max-width: 760px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  h2 { font-size: 17px; margin: 28px 0 6px; }
  nav a { margin-right: 12px; }
  nav a[aria-current] { font-weight: 700; text-decoration: none; color: inherit; }
  .wrap { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; white-space: nowrap; }
  th, td { padding: 5px 8px; border-bottom: 1px solid var(--line); text-align: left; }
  th { color: var(--dim); font-weight: 500; font-size: 13px; }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  .note { color: var(--dim); font-size: 13px; }
</style>
<h1>服刑日誌排行榜</h1>
<nav><a href="?" data-days="">全部</a><a href="?days=30" data-days="30">近 30 天</a><a href="?days=7" data-days="7">近 7 天</a></nav>
<p class="note" id="note">載入中…</p>
<h2>總經驗</h2><div class="wrap"><table id="exp"></table></div>
<h2>最高效率<span class="note">（單段活躍滿 10 分鐘）</span></h2><div class="wrap"><table id="rate"></table></div>
<h2>等級</h2><div class="wrap"><table id="level"></table></div>
<script>
const days = new URLSearchParams(location.search).get("days") || "";
for (const a of document.querySelectorAll("nav a")) if (a.dataset.days === days) a.setAttribute("aria-current", "page");
const num = (n) => Math.round(n).toLocaleString();
const dur = (s) => `${Math.floor(s / 3600)} 小時 ${Math.floor(s % 3600 / 60)} 分`;
const BOARDS = {
  exp: [["角色", "character"], ["職業", "job"], ["等級", "level", String], ["累計經驗", "exp", num], ["活躍時間", "activeSec", dur], ["段數", "records", num]],
  rate: [["角色", "character"], ["職業", "job"], ["等級", "level", String], ["地圖", "mapName"], ["EXP / hr", "rate", num], ["活躍時間", "activeSec", dur]],
  level: [["角色", "character"], ["職業", "job"], ["等級", "level", String]],
};
function fill(id, rows) {
  const table = document.getElementById(id), cols = BOARDS[id];
  const head = table.createTHead().insertRow();
  for (const [label, , fmt] of [["#", "", num], ...cols]) {
    const th = document.createElement("th");
    th.textContent = label;
    if (fmt) th.className = "num";
    head.appendChild(th);
  }
  const body = table.createTBody();
  rows.forEach((row, i) => {
    const tr = body.insertRow();
    const rank = tr.insertCell();
    rank.textContent = i + 1;
    rank.className = "num";
    for (const [, key, fmt] of cols) {
      const td = tr.insertCell();
      const value = row[key];
      td.textContent = value == null || value === "" ? "--" : fmt ? fmt(value) : value;
      if (fmt) td.className = "num";
    }
  });
  if (!rows.length) body.insertRow().insertCell().textContent = "還沒有紀錄";
}
fetch("api/leaderboard" + (days ? `?days=${days}` : "")).then((res) => res.json()).then((data) => {
  for (const id of Object.keys(BOARDS)) fill(id, data[id]);
  document.getElementById("note").textContent = "資料來自大家在服刑日誌結算時上傳的修練紀錄。";
}).catch(() => { document.getElementById("note").textContent = "讀不到排行榜，稍後再試。"; });
</script>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "PrisonDiary"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 15          # 連上來不講話的連線 15 秒就斷，免得佔著執行緒

    store: Store
    limiter: RateLimiter

    # ------------------------------------------------------------ 回應
    def _send(self, status: int, body: bytes = b"", ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        # 網頁在 GitHub Pages、後端在別的網域，瀏覽器要看到這些標頭才肯送；沒有帳號與 cookie，開放給所有來源沒有風險
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        if body:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: object) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def _error(self, status: int, message: str) -> None:
        # 出錯就不保留連線：請求的內容可能還沒讀完，留著會把下一個請求讀歪
        self.close_connection = True
        self._json(status, {"error": message})

    def log_request(self, code="-", size="-") -> None:
        # 正常的請求不寫 log（journal 也在 SD 卡上），只留出錯的
        if isinstance(code, int) and code < 400:
            return
        super().log_request(code, size)

    def _guard(self) -> str | None:
        ip = client_ip(self.client_address[0], self.headers)
        if not self.limiter.allow(ip):
            self._error(429, "請求太頻繁，稍後再試")
            return None
        return ip

    # ------------------------------------------------------------ 路由
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if self._guard() is None:
            return
        url = urlsplit(self.path)
        if url.path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif url.path == "/api/health":
            self._json(200, {"ok": True, "records": self.store.count()})
        elif url.path == "/api/leaderboard":
            query = parse_qs(url.query)
            try:
                days = int(query["days"][0]) if "days" in query else None
                limit = int(query["limit"][0]) if "limit" in query else 50
            except ValueError:
                self._error(400, "days 與 limit 要是整數")
                return
            if (days is not None and not 1 <= days <= 3650) or not 1 <= limit <= MAX_LIMIT:
                self._error(400, "days 或 limit 超出範圍")
                return
            self._json(200, self.store.boards(days, limit))
        else:
            self._error(404, "沒有這個路徑")

    do_HEAD = do_GET

    def do_POST(self) -> None:
        ip = self._guard()
        if ip is None:
            return
        if urlsplit(self.path).path != "/api/records":
            self._error(404, "沒有這個路徑")
            return
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            self._error(411, "要有 Content-Length")
            return
        if not 0 < length <= MAX_BODY:
            self._error(413, "內容太大")
            return
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            self._error(400, "不是合法的 JSON")
            return
        records = data.get("records") if isinstance(data, dict) else None
        if not isinstance(records, list) or not 0 < len(records) <= MAX_BATCH:
            self._error(400, f"records 要是 1 到 {MAX_BATCH} 筆的陣列")
            return
        try:
            accepted, rejected = self.store.add(records, ip)
        except Full as err:
            self._error(507, str(err))
            return
        self._json(200, {"accepted": accepted, "rejected": rejected})

    def do_DELETE(self) -> None:
        if self._guard() is None:
            return
        prefix = "/api/records/"
        path = urlsplit(self.path).path
        uid = path[len(prefix):] if path.startswith(prefix) else ""
        if not UID.match(uid):
            self._error(404, "沒有這個路徑")
            return
        self.store.delete(uid)
        self._send(204)


class Server(ThreadingHTTPServer):
    daemon_threads = True


def make_server(host: str, port: int, store: Store, limiter: RateLimiter | None = None) -> Server:
    handler = type("BoundHandler", (Handler,), {"store": store, "limiter": limiter or RateLimiter()})
    return Server((host, port), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="服刑日誌後端：收修練紀錄、算排行榜")
    parser.add_argument("--host", default="127.0.0.1", help="監聽位址；要讓區網連就給 0.0.0.0（預設只給本機）")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--db", default=str(Path(__file__).resolve().parent / "data" / "records.db"))
    args = parser.parse_args(argv)
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    store = Store(args.db)
    server = make_server(args.host, args.port, store)
    print(f"listening on http://{args.host}:{args.port}  db={args.db}  records={store.count()}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
