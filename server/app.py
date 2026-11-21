"""服刑日誌的後端：收網頁上傳的修練紀錄，存進 SQLite，算排行榜。

只用標準函式庫，放在記憶體與空間都很小的機器（Raspberry Pi 3）上跑：不用裝套件，
資料就是一個 SQLite 檔，備份時複製那個檔就好。

沒有登入也沒有帳號：一筆紀錄只靠網頁給的 uid 辨認，內容不比對。網頁每次開啟都會把手上的
紀錄整批重傳，已經有的 uid 不會再存一次（uid 是網頁從紀錄內容算出來的指紋，同一筆永遠同一個）。
活躍不滿 10 分鐘的紀錄不收（網頁也不會傳）。收下的紀錄不會因為使用者在網頁上刪掉而消失：沒有對外的刪除功能，要拿掉只能直接改資料檔。
uid 與上傳者的 IP 只存不對外，任何回應裡都不會出現。

每個瀏覽器有一把自己產生的金鑰，上傳紀錄時帶著；這裡記下每筆紀錄是哪把金鑰傳的（只存雜湊）。
它不是登入，只用來決定誰能幫一個角色設排行榜上的造型：傳過那個角色紀錄的金鑰都可以，
同一個角色在兩台電腦都有紀錄就兩邊都能改，後設的蓋掉先設的。造型只是紙娃娃的零件編號，不是圖片。

數字是瀏覽器自己算好傳上來的，這裡只擋格式不對與明顯不可能的值，擋不了有心造假。

格式對的紀錄都收下，但只有「算數」的才進排行榜：要有等級、職業在合法清單裡、地圖名稱在合法清單裡、
而且不是女神組隊任務的地圖（解任務拿的經驗不算練功）。清單在旁邊的 names.json（scripts/export_server_names.py 產生），
部署時跟這個檔一起複製。不算數的照樣存著，原因記在 status 欄，回應的 uncounted 也會講，之後可以直接查資料檔
找出是哪裡辨識錯；啟動時每一筆都照目前的清單重算一次，所以清單改了，舊紀錄也會跟著進出排行榜。

這裡只出資料，畫面在網站的「排行榜」頁籤（web/index.html）。

診斷快照與問題回報：網頁每結算一段紀錄、或辨識一直讀不到東西時，會把辨識用到的那幾塊畫面（原尺寸的小裁切圖）
與當時的引擎狀態傳上來（/api/snapshots）；使用者按「回報問題」時另外帶整張畫面、描述與 log（/api/reports）。
圖片存在資料檔旁邊的 snapshots/<代碼>/ 資料夾，資料表只記索引。自動快照留 30 天、回報留 180 天，
全部超過 300 MB 就從最舊的刪。回報另外轉發到 Discord（webhook 網址放在環境變數 PRISONDIARY_DISCORD_WEBHOOK
或這個檔旁邊的 discord_webhook.txt；沒設就只存在這裡）。這些都只收不出，查看時直接讀資料夾。

路徑：
    POST   /api/records        {"records": [...]}，回 {"accepted": [uid], "rejected": [{uid, reason}], "uncounted": [{uid, reason}]}
                               rejected 是格式不對、沒存的；uncounted 是存了但不算排行榜的（也在 accepted 裡）
    POST   /api/records        同上，多帶 "key"（瀏覽器的金鑰）就把這批紀錄記在它名下；已經有、還沒主的紀錄也歸它
    POST   /api/looks          {"key", "character", "look"}，設那個角色的造型；look 給 null 是拿掉。沒傳過那個角色的紀錄回 403
    POST   /api/looks/mine     {"key"}，回這把金鑰名下的角色與各自目前的造型 {"characters": [{character, look}]}
    GET    /api/leaderboard    ?days=7&limit=50，回三個個人榜（總經驗、最高效率、等級），looks 是榜上有設造型的角色各自的造型
    GET    /api/training       ?days=7&job=法師&level=35&limit=100，回練功榜：地圖 × 職業 × 等級區間的平均效率
    POST   /api/glyphs         {"key"?, "glyphs": [{kind, char, h, w, bits, value, frame, source, version}]}，
                               網頁靠 OCR 學會的字形（楓幣、等級），回 {"accepted": 筆數, "rejected": 筆數}。
                               只收不出：之後直接讀資料檔，整理各台電腦學到的長相、挑出可以放進內建字形的
    POST   /api/snapshots      {"key"?, "kind": "segment"|"anomaly", "uid"?, "reason"?, "version", "state", "crops": {名稱: PNG}}，
                               回 {"code"}。segment 是一段紀錄的快照（uid 是那筆紀錄），anomaly 是辨識一直讀不到東西時拍的
    POST   /api/reports        {"key"?, "version", "note", "state", "log", "crops", "frame": JPEG}，回 {"code", "relayed"}；
                               relayed 是有沒有設 Discord（轉發在背景做，失敗只寫 log）
    GET    /api/health         {"ok": true, "records": 筆數, "counted": 算進排行榜的筆數}
    GET    /                   轉去網站的排行榜頁籤
    POST   /api/records/delete、DELETE /api/records/<uid>
                               舊版網頁刪紀錄時會叫的；現在不刪了，照樣回 204，舊網頁才不會卡著一直重試
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import ipaddress
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

MAX_BODY = 128 * 1024            # 一次請求的上限；一批 100 筆大約 30 KB
MAX_BATCH = 100
MAX_DB_BYTES = 200 * 1024 * 1024  # 資料檔長到這麼大就不再收，免得把 SD 卡塞滿
MIN_FREE_BYTES = 100 * 1024 * 1024
RATE_LIMIT = 120                  # 每個 IP 每分鐘幾個請求
RATE_WINDOW = 60.0
MIN_ACTIVE_SEC = 600              # 活躍不滿 10 分鐘的紀錄不收：太短的效率不準，打幾隻怪就能衝上榜
BOARD_TTL = 60.0
MAX_LIMIT = 200
LEVEL_BAND = 10                   # 練功榜的等級區間：1–10、11–20…
SITE = "https://andy971029.github.io/PrisonDiary/"
LOOK_PARTS = 24                   # 一套造型最多幾個部位；紙娃娃現在有 12 類時裝
LOOK_PART = re.compile(r"[A-Za-z]{1,24}\Z")
MAX_GLYPHS = 200                  # 一次最多回報幾個字形；一個約 150 位元組
GLYPH_KINDS = ("meso", "level")
GLYPH_MAX_SIDE = 40               # 字形最大邊長；原尺寸的數字是 5x9 上下
GLYPH_VALUE = re.compile(r"[0-9]{1,13}\Z")

NAMES_PATH = Path(__file__).resolve().parent / "names.json"
SNAP_KINDS = ("segment", "anomaly")
MAX_SNAP_BODY = 256 * 1024        # 自動快照：原尺寸的小裁切圖，一份通常不到 20 KB
MAX_REPORT_BODY = 6 * 1024 * 1024  # 回報帶整張畫面（JPEG）
SNAP_CROPS = 16                   # 一段紀錄的快照有開頭、最後各一組，一組最多 8 塊
SNAP_CROP_BYTES = 96 * 1024
SNAP_NAME = re.compile(r"[a-z][a-z0-9_-]{0,23}\Z")
SNAP_STATE_BYTES = 64 * 1024
REPORT_NOTE = 2000
REPORT_LOG = 30_000
REPORT_FRAME_BYTES = 4 * 1024 * 1024
SNAP_DAILY = 300                  # 一個瀏覽器一天最多幾份自動快照
REPORT_DAILY = 30                 # 一個瀏覽器一天最多回報幾次
SNAP_KEEP_MS = 30 * 86_400_000
REPORT_KEEP_MS = 180 * 86_400_000
SNAP_MAX_BYTES = 300 * 1024 * 1024
CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 沒有 0/O、1/I，念給別人聽不會搞混
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
DISCORD_ENV = "PRISONDIARY_DISCORD_WEBHOOK"
DISCORD_PREFIXES = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")
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
    received_at INTEGER NOT NULL,
    owner       TEXT,
    status      TEXT NOT NULL DEFAULT ''   -- 空字串是算進排行榜；不然是不算的原因（見 status_of）
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS records_character ON records (character, end_at);
CREATE INDEX IF NOT EXISTS records_end ON records (end_at);
CREATE TABLE IF NOT EXISTS glyphs (
    kind        TEXT NOT NULL,
    char        TEXT NOT NULL,
    h           INTEGER NOT NULL,
    w           INTEGER NOT NULL,
    bits        TEXT NOT NULL,
    reporter    TEXT NOT NULL,     -- 瀏覽器金鑰的雜湊；沒帶金鑰的是 "ip:" 加 IP。同一台回報同一個字形只記一次
    value       TEXT NOT NULL,     -- 學到它的時候 OCR 讀出來的整個數字
    frame_w     INTEGER NOT NULL,  -- 辨識用的畫面大小（遊戲被放大顯示時是縮回去的大小）
    frame_h     INTEGER NOT NULL,
    source_w    INTEGER NOT NULL,  -- 分享進來的原始畫面大小
    source_h    INTEGER NOT NULL,
    version     TEXT NOT NULL,
    ip          TEXT NOT NULL,
    received_at INTEGER NOT NULL,
    PRIMARY KEY (kind, char, h, w, bits, reporter)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS snapshots (
    code        TEXT PRIMARY KEY,  -- 六個字的代碼，也是 snapshots/ 底下的資料夾名稱
    kind        TEXT NOT NULL,     -- segment、anomaly 或 report
    uid         TEXT,              -- segment：那段紀錄的 uid（讀不到角色名字的紀錄沒有）
    reason      TEXT NOT NULL DEFAULT '',  -- anomaly：讀不到什麼；segment：沒有 uid 的原因
    note        TEXT NOT NULL DEFAULT '',  -- report：使用者寫的描述
    character   TEXT NOT NULL DEFAULT '',
    version     TEXT NOT NULL,
    reporter    TEXT NOT NULL,     -- 瀏覽器金鑰的雜湊；沒帶金鑰的是 "ip:" 加 IP
    ip          TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    bytes       INTEGER NOT NULL,  -- 資料夾裡的檔案加起來多大
    files       TEXT NOT NULL      -- 資料夾裡的檔名，逗號分隔
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS snapshots_uid ON snapshots (uid);
CREATE INDEX IF NOT EXISTS snapshots_created ON snapshots (created_at);
CREATE TABLE IF NOT EXISTS looks (
    character  TEXT PRIMARY KEY,
    look       TEXT NOT NULL,
    owner      TEXT NOT NULL,
    ip         TEXT NOT NULL,
    updated_at INTEGER NOT NULL
) WITHOUT ROWID;
"""


class Invalid(ValueError):
    """上傳的紀錄不合格；訊息會回給網頁。"""


def _text(raw: dict, key: str, limit: int, *, required: bool = False, multiline: bool = False) -> str:
    value = raw.get(key)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise Invalid(f"{key} 不是文字")
    value = value.strip()
    allowed = "\n\t" if multiline else ""
    if len(value) > limit or any((ord(ch) < 32 and ch not in allowed) or ord(ch) == 127 for ch in value):
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
    active_sec = float(_number(raw, "activeSec", 0, MAX_SEC))
    if active_sec < MIN_ACTIVE_SEC:
        raise Invalid("活躍時間不滿 10 分鐘")
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
        "active_sec": active_sec,
        "idle_sec": float(_number(raw, "idleSec", 0, MAX_SEC)),
    }


class Names:
    """後端認得的職業與地圖名稱（names.json）。"""

    def __init__(self, jobs, maps, no_board_maps=()) -> None:
        self.jobs = frozenset(jobs)
        self.maps = frozenset(maps)
        self.no_board_maps = frozenset(no_board_maps)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Names":
        path = NAMES_PATH if path is None else Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        names = cls(data["jobs"], data["maps"], data.get("noBoardMaps", ()))
        if not names.jobs or not names.maps:
            raise ValueError(f"{path} 的職業或地圖清單是空的")
        return names


def status_of(row: dict, names: Names) -> str:
    """一筆紀錄算不算排行榜：算就回空字串，不算回原因。角色名字、10 分鐘在 validate 就擋掉了。"""
    if row["level"] is None:
        return "沒有等級"
    if not row["job"]:
        return "沒有職業"
    if row["job"] not in names.jobs:
        return "職業不在清單裡"
    if not row["map_name"]:
        return "沒有地圖名稱"
    if row["map_name"] not in names.maps:
        return "地圖不在清單裡"
    if row["map_name"] in names.no_board_maps:
        return "女神組隊任務的地圖"
    return ""


def _look_int(value: object, name: str, high: int, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= high:
        raise Invalid(f"造型的 {name} 不對")
    return value


def validate_look(raw: object) -> dict:
    """檢查網頁傳來的一套造型，只留認得的欄位。

    造型是紙娃娃的零件編號：膚色、髮型、臉型、各自的混色、每個部位穿哪一件、武器類型。
    這裡只擋格式；編號存不存在由網頁畫的時候處理（下一版客戶端拿掉的零件，網頁會當作沒穿）。
    """
    if not isinstance(raw, dict):
        raise Invalid("造型不是物件")
    mix = raw.get("mix") if raw.get("mix") is not None else {}
    worn = raw.get("worn") if raw.get("worn") is not None else {}
    if not isinstance(mix, dict) or not isinstance(worn, dict) or len(worn) > LOOK_PARTS:
        raise Invalid("造型的 mix 或 worn 不對")
    for part in worn:
        if not isinstance(part, str) or not LOOK_PART.match(part):
            raise Invalid("造型的部位名稱不對")
    return {
        "skin": _look_int(raw.get("skin"), "skin", 999),
        "hair": _look_int(raw.get("hair"), "hair", 9_999_999),
        "face": _look_int(raw.get("face"), "face", 9_999_999),
        "mix": {kind: _look_int(mix.get(kind), "mix", 99, optional=True) for kind in ("hair", "face")},
        "worn": {part: _look_int(worn[part], part, 99_999_999) for part in sorted(worn)},
        "group": _look_int(raw.get("group"), "group", 999, optional=True),
    }


def _size(raw: dict, key: str) -> tuple[int, int]:
    value = raw.get(key)
    if (not isinstance(value, list) or len(value) != 2
            or not all(isinstance(v, int) and not isinstance(v, bool) and 0 < v <= 10000 for v in value)):
        raise Invalid(f"{key} 要是 [寬, 高]")
    return value[0], value[1]


def validate_glyph(raw: object) -> dict:
    """網頁回報的一個字形，檢查過轉成資料表的欄位；不合格丟 Invalid。"""
    if not isinstance(raw, dict):
        raise Invalid("不是物件")
    kind = raw.get("kind")
    if kind not in GLYPH_KINDS:
        raise Invalid("kind 不對")
    char = raw.get("char")
    if not isinstance(char, str) or len(char) != 1 or not char.isdigit() or not char.isascii():
        raise Invalid("char 要是一個數字")
    h = int(_number(raw, "h", 1, GLYPH_MAX_SIDE))
    w = int(_number(raw, "w", 1, GLYPH_MAX_SIDE))
    if raw["h"] != h or raw["w"] != w:
        raise Invalid("h、w 要是整數")
    bits = raw.get("bits")
    try:
        packed = base64.b64decode(bits, validate=True) if isinstance(bits, str) else None
    except (ValueError, binascii.Error):
        packed = None
    if packed is None or len(packed) != (h * w + 7) // 8:
        raise Invalid("bits 跟 h、w 對不上")
    value = raw.get("value")
    if not isinstance(value, str) or not GLYPH_VALUE.match(value) or char not in value:
        raise Invalid("value 不對")
    frame_w, frame_h = _size(raw, "frame")
    source_w, source_h = _size(raw, "source")
    return {
        "kind": kind, "char": char, "h": h, "w": w, "bits": bits, "value": value,
        "frame_w": frame_w, "frame_h": frame_h, "source_w": source_w, "source_h": source_h,
        "version": _text(raw, "version", 20),
    }


def _blob(value: object, magic: bytes, limit: int, name: str) -> bytes:
    """base64（可以帶 data: 開頭）解成位元組，檢查檔頭與大小。"""
    if not isinstance(value, str) or len(value) > limit * 4 // 3 + 64:
        raise Invalid(f"{name} 不是文字或太大")
    if value.startswith("data:"):
        value = value.partition(",")[2]
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise Invalid(f"{name} 不是 base64") from None
    if not data.startswith(magic) or len(data) > limit:
        raise Invalid(f"{name} 格式不對或太大")
    return data


def _state_json(value: object) -> str:
    if not isinstance(value, dict):
        raise Invalid("state 要是物件")
    text = json.dumps(value, ensure_ascii=False, indent=1)
    if len(text.encode("utf-8")) > SNAP_STATE_BYTES:
        raise Invalid("state 太大")
    return text


def validate_snapshot(raw: object, *, report: bool = False) -> dict:
    """自動快照或問題回報，檢查過轉成要存的內容；不合格丟 Invalid。"""
    if not isinstance(raw, dict):
        raise Invalid("不是物件")
    out = {
        "kind": "report",
        "uid": None,
        "reason": "",
        "note": "",
        "version": _text(raw, "version", 24, required=True),
        "state": _state_json(raw.get("state")),
        "log": "",
        "frame": None,
    }
    state = raw["state"]
    character = state.get("character")
    out["character"] = character.strip()[:24] if isinstance(character, str) else ""
    if report:
        out["note"] = _text(raw, "note", REPORT_NOTE, multiline=True)
        out["log"] = _text(raw, "log", REPORT_LOG, multiline=True)
        if raw.get("frame") is not None:
            out["frame"] = _blob(raw["frame"], JPEG_MAGIC, REPORT_FRAME_BYTES, "frame")
    else:
        kind = raw.get("kind")
        if kind not in SNAP_KINDS:
            raise Invalid("kind 不對")
        out["kind"] = kind
        uid = raw.get("uid")
        if uid is not None and (not isinstance(uid, str) or not UID.match(uid)):
            raise Invalid("uid 格式不對")
        out["uid"] = uid
        out["reason"] = _text(raw, "reason", 48)
        if kind == "anomaly" and not out["reason"]:
            raise Invalid("anomaly 要有 reason")
    crops = raw.get("crops") if raw.get("crops") is not None else {}
    if not isinstance(crops, dict) or len(crops) > SNAP_CROPS:
        raise Invalid(f"crops 要是最多 {SNAP_CROPS} 塊的物件")
    out["crops"] = {}
    for name, value in crops.items():
        if not isinstance(name, str) or not SNAP_NAME.match(name):
            raise Invalid("crops 的名稱不對")
        out["crops"][name] = _blob(value, PNG_MAGIC, SNAP_CROP_BYTES, f"crops.{name}")
    return out


def new_code() -> str:
    return "".join(secrets.choice(CODE_CHARS) for _ in range(6))


def owner_of(key: object) -> str | None:
    """瀏覽器金鑰的雜湊，資料表裡存的是它；金鑰格式不對回 None。"""
    if not isinstance(key, str) or not UID.match(key):
        return None
    return hashlib.sha256(key.encode("ascii")).hexdigest()


class Full(RuntimeError):
    """資料檔或磁碟滿了，不再收新紀錄。"""


class Busy(RuntimeError):
    """同一個瀏覽器今天傳太多快照或回報了。"""


class Store:
    """SQLite 的包裝。只有一條連線，所有讀寫排隊（量很小，不需要連線池）。"""

    def __init__(self, path: str | Path, *, names: Names | None = None, snap_dir: str | Path | None = None,
                 max_bytes: int = MAX_DB_BYTES, min_free: int = MIN_FREE_BYTES,
                 snap_max_bytes: int = SNAP_MAX_BYTES) -> None:
        self.path = str(path)
        self.names = names or Names.load()
        # 快照的圖片放在資料檔旁邊；記憶體裡的資料庫（測試）要另外給資料夾，沒給就不收快照
        if snap_dir is None and self.path != ":memory:":
            snap_dir = Path(self.path).parent / "snapshots"
        self.snap_dir = Path(snap_dir) if snap_dir is not None else None
        if self.snap_dir is not None:
            self.snap_dir.mkdir(parents=True, exist_ok=True)
        self.snap_max_bytes = snap_max_bytes
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
        # owner 欄是後來才加的：舊的資料檔補上這一欄（值是空的，等那個瀏覽器下次開頁重傳時認領）
        if "owner" not in {row[1] for row in self._db.execute("PRAGMA table_info(records)")}:
            self._db.execute("ALTER TABLE records ADD COLUMN owner TEXT")
        self._db.execute("CREATE INDEX IF NOT EXISTS records_owner ON records (owner, character)")
        # status 欄也是後來才加的；不管新舊，每次啟動都照目前的名稱清單重算一次
        if "status" not in {row[1] for row in self._db.execute("PRAGMA table_info(records)")}:
            self._db.execute("ALTER TABLE records ADD COLUMN status TEXT NOT NULL DEFAULT ''")
        self._restatus()
        # 門檻是後來才加的：之前收下的短紀錄在啟動時清掉
        self._db.execute("DELETE FROM records WHERE active_sec < ?", (MIN_ACTIVE_SEC,))
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _restatus(self) -> None:
        cur = self._db.execute("SELECT uid, level, job, map_name, status FROM records")
        cols = [d[0] for d in cur.description]
        changes = []
        for values in cur.fetchall():
            row = dict(zip(cols, values))
            status = status_of(row, self.names)
            if status != row["status"]:
                changes.append((status, row["uid"]))
        self._db.executemany("UPDATE records SET status = ? WHERE uid = ?", changes)

    def _check_room(self) -> None:
        if self.path == ":memory:":
            return
        target = Path(self.path)
        if target.stat().st_size > self.max_bytes:
            raise Full("資料檔已達上限")
        if shutil.disk_usage(target.parent).free < self.min_free:
            raise Full("磁碟空間不足")

    def add(self, raws: list, ip: str, now_ms: int | None = None, *,
            owner: str | None = None) -> tuple[list[str], list[dict], list[dict]]:
        """存一批紀錄。回 (收下的 uid, 被拒絕的 {uid, reason}, 收下但不算排行榜的 {uid, reason})；已經有的 uid 也算收下。

        owner 是上傳的瀏覽器（金鑰的雜湊）。已經有、但還沒記主的紀錄（這個功能上線前收的）也歸它；
        已經有主的不改，先傳的人是主。
        """
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        accepted: list[str] = []
        rejected: list[dict] = []
        uncounted: list[dict] = []
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
            row["owner"] = owner
            row["status"] = status_of(row, self.names)
            rows.append(row)
            accepted.append(row["uid"])
            if row["status"]:
                uncounted.append({"uid": row["uid"], "reason": row["status"]})
        if rows:
            with self._lock:
                self._check_room()
                self._db.executemany(
                    "INSERT INTO records (uid, character, job, level, map_id, map_name, start_at, end_at,"
                    " cum_net, active_sec, idle_sec, ip, received_at, owner, status)"
                    " VALUES (:uid, :character, :job, :level, :map_id, :map_name, :start_at, :end_at,"
                    " :cum_net, :active_sec, :idle_sec, :ip, :received_at, :owner, :status)"
                    " ON CONFLICT (uid) DO NOTHING",
                    rows,
                )
                if owner:
                    self._db.executemany(
                        "UPDATE records SET owner = ? WHERE uid = ? AND owner IS NULL",
                        [(owner, row["uid"]) for row in rows],
                    )
                self._db.commit()
                self._boards.clear()
        return accepted, rejected, uncounted

    def add_glyphs(self, raws: list, ip: str, now_ms: int | None = None, *,
                   owner: str | None = None) -> tuple[int, int]:
        """存一批回報的字形。回 (收下幾個, 不合格幾個)；同一台已經回報過的同一個字形也算收下。"""
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        rows = []
        rejected = 0
        for raw in raws:
            try:
                row = validate_glyph(raw)
            except Invalid:
                rejected += 1
                continue
            row.update(reporter=owner or f"ip:{ip}", ip=ip, received_at=now_ms)
            rows.append(row)
        if rows:
            with self._lock:
                self._check_room()
                self._db.executemany(
                    "INSERT INTO glyphs (kind, char, h, w, bits, reporter, value, frame_w, frame_h,"
                    " source_w, source_h, version, ip, received_at)"
                    " VALUES (:kind, :char, :h, :w, :bits, :reporter, :value, :frame_w, :frame_h,"
                    " :source_w, :source_h, :version, :ip, :received_at)"
                    " ON CONFLICT DO NOTHING",
                    rows,
                )
                self._db.commit()
        return len(rows), rejected

    def set_look(self, owner: str, character: str, look: dict | None, ip: str, now_ms: int | None = None) -> bool:
        """設（look 是 None 就拿掉）一個角色的造型。這把金鑰沒傳過那個角色的紀錄就不動，回 False。"""
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        with self._lock:
            mine = self._db.execute(
                "SELECT 1 FROM records WHERE owner = ? AND character = ? LIMIT 1", (owner, character)).fetchone()
            if not mine:
                return False
            if look is None:
                self._db.execute("DELETE FROM looks WHERE character = ?", (character,))
            else:
                self._check_room()
                self._db.execute(
                    "INSERT INTO looks (character, look, owner, ip, updated_at) VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT (character) DO UPDATE SET look = excluded.look, owner = excluded.owner,"
                    " ip = excluded.ip, updated_at = excluded.updated_at",
                    (character, json.dumps(look, separators=(",", ":")), owner, ip, now_ms),
                )
            self._db.commit()
            self._boards.clear()
        return True

    def my_looks(self, owner: str) -> list[dict]:
        """這把金鑰名下的角色（傳過紀錄的），以及各自目前的造型（沒設是 None；可能是別台電腦設的）。"""
        with self._lock:
            rows = self._db.execute(
                "SELECT DISTINCT r.character, l.look FROM records r LEFT JOIN looks l ON l.character = r.character"
                " WHERE r.owner = ? ORDER BY r.character", (owner,)).fetchall()
        return [{"character": c, "look": json.loads(look) if look else None} for c, look in rows]

    def add_snapshot(self, snap: dict, reporter: str, ip: str, now_ms: int | None = None) -> str:
        """存一份快照或回報，回代碼。圖片寫進 snapshots/<代碼>/，資料表只記索引；順便清掉過期的。"""
        if self.snap_dir is None:
            raise Full("沒有放快照的地方")
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        files = {"state.json": snap["state"].encode("utf-8")}
        if snap["note"] or snap["log"]:
            files["report.txt"] = f"{snap['note']}\n\n----- log -----\n{snap['log']}\n".encode("utf-8")
        if snap["frame"]:
            files["frame.jpg"] = snap["frame"]
        for name, data in snap["crops"].items():
            files[f"{name}.png"] = data
        size = sum(len(data) for data in files.values())
        report = snap["kind"] == "report"
        with self._lock:
            self._check_room()
            if shutil.disk_usage(self.snap_dir).free < self.min_free:
                raise Full("磁碟空間不足")
            daily = self._db.execute(
                "SELECT COUNT(*) FROM snapshots WHERE reporter = ? AND created_at > ? AND (kind = 'report') = ?",
                (reporter, now_ms - DAY_MS, report),
            ).fetchone()[0]
            if daily >= (REPORT_DAILY if report else SNAP_DAILY):
                raise Busy("今天傳的已經夠多了，明天再來")
            code = new_code()
            while self._db.execute("SELECT 1 FROM snapshots WHERE code = ?", (code,)).fetchone():
                code = new_code()
            folder = self.snap_dir / code
            folder.mkdir(parents=True)
            for name, data in files.items():
                (folder / name).write_bytes(data)
            self._db.execute(
                "INSERT INTO snapshots (code, kind, uid, reason, note, character, version, reporter, ip, created_at, bytes, files)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (code, snap["kind"], snap["uid"], snap["reason"], snap["note"], snap["character"], snap["version"],
                 reporter, ip, now_ms, size, ",".join(files)),
            )
            self._db.commit()
            self._prune(now_ms)
        return code

    def _prune(self, now_ms: int) -> None:
        """自動快照留 30 天、回報留 180 天；全部超過上限就從最舊的刪（自動快照先刪）。呼叫前要拿著鎖。"""
        old = self._db.execute(
            "SELECT code FROM snapshots WHERE (kind = 'report' AND created_at < ?) OR (kind <> 'report' AND created_at < ?)",
            (now_ms - REPORT_KEEP_MS, now_ms - SNAP_KEEP_MS),
        ).fetchall()
        doomed = [code for (code,) in old]
        total = self._db.execute("SELECT COALESCE(SUM(bytes), 0) FROM snapshots").fetchone()[0]
        total -= sum(self._db.execute("SELECT bytes FROM snapshots WHERE code = ?", (c,)).fetchone()[0] for c in doomed)
        if total > self.snap_max_bytes:
            rest = self._db.execute(
                "SELECT code, bytes FROM snapshots ORDER BY kind = 'report', created_at"
            ).fetchall()
            for code, size in rest:
                if total <= self.snap_max_bytes:
                    break
                if code in doomed:
                    continue
                doomed.append(code)
                total -= size
        for code in doomed:
            shutil.rmtree(self.snap_dir / code, ignore_errors=True)
        if doomed:
            self._db.executemany("DELETE FROM snapshots WHERE code = ?", [(c,) for c in doomed])
            self._db.commit()

    def count(self, *, counted: bool = False) -> int:
        with self._lock:
            where = " WHERE status = ''" if counted else ""
            return self._db.execute(f"SELECT COUNT(*) FROM records{where}").fetchone()[0]

    def boards(self, days: int | None = None, limit: int = 50, now_ms: int | None = None) -> dict:
        """三個榜，每個角色一列。days 給了就只算結束時間在最近這幾天內的紀錄。不算數的紀錄（status 不是空的）不看。"""
        now = time.monotonic()
        key = (days, limit)
        with self._lock:
            hit = self._boards.get(key)
            if hit and now - hit[0] < BOARD_TTL and now_ms is None:
                return hit[1]
            now_ms = int(time.time() * 1000) if now_ms is None else now_ms
            since = now_ms - days * DAY_MS if days else 0
            # 職業取該角色最近一筆有讀到職業的（轉職後名字不變、職業會變）
            job = ("(SELECT job FROM records j WHERE j.character = r.character AND j.job <> '' AND j.status = ''"
                   " ORDER BY j.end_at DESC LIMIT 1)")
            exp = self._db.execute(
                f"SELECT character, {job}, MAX(level), SUM(cum_net) AS total, SUM(active_sec), COUNT(*)"
                " FROM records r WHERE end_at >= ? AND status = '' GROUP BY character ORDER BY total DESC, character LIMIT ?",
                (since, limit),
            ).fetchall()
            # 只有一個 MAX() 時，SQLite 保證沒聚合的欄位取自最大值那一列，所以等級、地圖是那一筆紀錄的
            rate = self._db.execute(
                "SELECT character, job, level, map_name, MAX(cum_net * 3600.0 / active_sec) AS best,"
                " active_sec, end_at"
                " FROM records r WHERE end_at >= ? AND status = ''"
                " GROUP BY character ORDER BY best DESC, character LIMIT ?",
                (since, limit),
            ).fetchall()
            level = self._db.execute(
                f"SELECT character, {job}, MAX(level) AS top, MIN(end_at)"
                " FROM records r WHERE end_at >= ? AND status = ''"
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
            # 這次回應裡出現的角色有設造型的都附上：頒獎台畫全身，名單畫頭像
            names = sorted({row[0] for board in (exp, rate, level) for row in board})
            looks = self._db.execute(
                f"SELECT character, look FROM looks WHERE character IN ({','.join('?' * len(names))})", names,
            ).fetchall() if names else []
            out["looks"] = {c: json.loads(look) for c, look in looks}
            self._boards[key] = (now, out)
            return out

    def training(self, days: int | None = None, job: str | None = None, level: int | None = None,
                 limit: int = 100, now_ms: int | None = None) -> dict:
        """練功榜：同一張地圖、同一個職業、同一個等級區間的紀錄合起來，總經驗 ÷ 總活躍時間，效率高的在前。

        地圖用名字分（同一張圖的像素指紋可能不只一個）；不算數的紀錄（沒讀到地圖、職業或等級、女神組隊任務等）不看。
        job、level 給了就只看那個職業、那個等級所在的區間。jobs、levels 是這段期間內有資料的選項，給網頁做篩選。
        """
        now = time.monotonic()
        key = ("training", days, job, level, limit)
        with self._lock:
            hit = self._boards.get(key)
            if hit and now - hit[0] < BOARD_TTL and now_ms is None:
                return hit[1]
            now_ms = int(time.time() * 1000) if now_ms is None else now_ms
            since = now_ms - days * DAY_MS if days else 0
            base = "end_at >= ? AND status = ''"
            band = f"(level - 1) / {LEVEL_BAND}"
            where, args = base, [since]
            if job:
                where += " AND job = ?"
                args.append(job)
            if level:
                where += f" AND {band} = ?"
                args.append((level - 1) // LEVEL_BAND)
            rows = self._db.execute(
                f"SELECT map_name, job, {band} AS band, SUM(cum_net) * 3600.0 / SUM(active_sec) AS rate,"
                " SUM(cum_net), SUM(active_sec), COUNT(*), COUNT(DISTINCT character)"
                f" FROM records WHERE {where} GROUP BY map_name, job, band"
                " ORDER BY rate DESC, map_name, job LIMIT ?",
                (*args, limit),
            ).fetchall()
            jobs = [j for (j,) in self._db.execute(f"SELECT DISTINCT job FROM records WHERE {base} ORDER BY job", (since,))]
            bands = [b for (b,) in self._db.execute(
                f"SELECT DISTINCT {band} AS band FROM records WHERE {base} ORDER BY band", (since,))]
            out = {
                "days": days,
                "jobs": jobs,
                "levels": [b * LEVEL_BAND + 1 for b in bands],
                "rows": [
                    {"mapName": m, "job": j, "levelFrom": b * LEVEL_BAND + 1, "levelTo": (b + 1) * LEVEL_BAND,
                     "rate": round(rate), "exp": total, "activeSec": round(sec), "records": n, "characters": who}
                    for m, j, b, rate, total, sec, n, who in rows
                ],
            }
            # 篩選條件是使用者給的，組合沒有上限；快取太多就整個清掉，免得被亂給的條件塞滿記憶體
            if len(self._boards) > 200:
                self._boards.clear()
            self._boards[key] = (now, out)
            return out


def discord_webhook() -> str | None:
    """Discord webhook 網址：環境變數優先，再來是 app.py 旁邊的 discord_webhook.txt；都沒有或格式不對回 None。"""
    url = os.environ.get(DISCORD_ENV, "").strip()
    if not url:
        try:
            url = (Path(__file__).resolve().parent / "discord_webhook.txt").read_text(encoding="utf-8").strip()
        except OSError:
            return None
    return url if url.startswith(DISCORD_PREFIXES) else None


def _multipart(payload: dict, files: list[tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = "prisondiary" + secrets.token_hex(12)
    chunks = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
        "Content-Type: application/json\r\n\r\n".encode("utf-8"),
        json.dumps(payload, ensure_ascii=False).encode("utf-8"), b"\r\n",
    ]
    for index, (name, data, ctype) in enumerate(files):
        chunks += [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[{index}]\"; filename=\"{name}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n".encode("utf-8"),
            data, b"\r\n",
        ]
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def discord_message(code: str, snap: dict, state: dict) -> tuple[dict, list[tuple[str, bytes, str]]]:
    """一則回報要送給 Discord 的內容：文字、整張畫面、狀態與 log、裁切圖（Discord 一則最多 10 個附件）。"""
    def show(value: object) -> str:
        return str(value) if value not in (None, "") else "--"

    lines = [
        f"**回報 #{code}**　v{snap['version']}",
        f"{show(state.get('character'))}｜{show(state.get('job'))}｜Lv. {show(state.get('level'))}｜{show(state.get('mapName'))}",
    ]
    if snap["note"]:
        lines += ["> " + line for line in snap["note"].splitlines()]
    content = "\n".join(lines)
    if len(content) > 1900:
        content = content[:1900] + "…（完整描述在 report.txt）"
    files: list[tuple[str, bytes, str]] = []
    if snap["frame"]:
        files.append(("frame.jpg", snap["frame"], "image/jpeg"))
    text = f"{snap['note']}\n\n----- state -----\n{snap['state']}\n\n----- log -----\n{snap['log']}\n"
    files.append(("report.txt", text.encode("utf-8"), "text/plain; charset=utf-8"))
    for name, data in list(snap["crops"].items())[: 10 - len(files)]:
        files.append((f"{name}.png", data, "image/png"))
    payload = {
        "content": content,
        "allowed_mentions": {"parse": []},     # 使用者寫的描述裡有 @everyone 也不會真的通知
        "attachments": [{"id": i, "filename": name} for i, (name, _, _) in enumerate(files)],
    }
    return payload, files


def relay_to_discord(url: str, code: str, snap: dict, state: dict) -> None:
    """在背景執行緒裡送；失敗只寫一行 log（回報已經存在主機上了）。"""
    payload, files = discord_message(code, snap, state)
    body, ctype = _multipart(payload, files)
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": ctype, "User-Agent": "PrisonDiary-backend (https://github.com/andy971029/PrisonDiary, 1)",
    })
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=30) as res:
                res.read()
            return
        except Exception as err:  # noqa: BLE001 - 網路錯誤、Discord 回 4xx/5xx 都一樣只記下來
            if attempt:
                print(f"Discord 轉發 #{code} 失敗：{err}", file=sys.stderr, flush=True)
            else:
                time.sleep(5)


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


class Handler(BaseHTTPRequestHandler):
    server_version = "PrisonDiary"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 15          # 連上來不講話的連線 15 秒就斷，免得佔著執行緒

    store: Store
    limiter: RateLimiter
    discord = staticmethod(discord_webhook)     # 測試換成假的

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
            # 排行榜的畫面在網站上；直接開後端網址的人帶過去
            self.send_response(302)
            self.send_header("Location", SITE + "#rank")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif url.path == "/api/health":
            self._json(200, {"ok": True, "records": self.store.count(), "counted": self.store.count(counted=True)})
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
        elif url.path == "/api/training":
            query = parse_qs(url.query)
            try:
                days = int(query["days"][0]) if "days" in query else None
                limit = int(query["limit"][0]) if "limit" in query else 100
                level = int(query["level"][0]) if "level" in query else None
            except ValueError:
                self._error(400, "days、level 與 limit 要是整數")
                return
            job = query["job"][0].strip() if "job" in query else None
            if ((days is not None and not 1 <= days <= 3650) or not 1 <= limit <= MAX_LIMIT
                    or (level is not None and not 1 <= level <= 300) or (job is not None and len(job) > 24)):
                self._error(400, "days、level、limit 或 job 超出範圍")
                return
            self._json(200, self.store.training(days, job or None, level, limit))
        else:
            self._error(404, "沒有這個路徑")

    do_HEAD = do_GET

    def do_POST(self) -> None:
        ip = self._guard()
        if ip is None:
            return
        path = urlsplit(self.path).path
        if path not in ("/api/records", "/api/records/delete", "/api/looks", "/api/looks/mine", "/api/glyphs",
                        "/api/snapshots", "/api/reports"):
            self._error(404, "沒有這個路徑")
            return
        limit = {"/api/snapshots": MAX_SNAP_BODY, "/api/reports": MAX_REPORT_BODY}.get(path, MAX_BODY)
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            self._error(411, "要有 Content-Length")
            return
        if not 0 < length <= limit:
            self._error(413, "內容太大")
            return
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            self._error(400, "不是合法的 JSON")
            return
        if path == "/api/records/delete":
            # 舊版網頁刪紀錄時叫的。現在收下的紀錄不刪，回 204 讓它把待刪清單清掉、繼續上傳
            self._send(204)
            return
        if not isinstance(data, dict):
            self._error(400, "內容要是一個物件")
            return
        # 金鑰：上傳紀錄與回報字形可以不帶（舊版網頁、存不了金鑰的瀏覽器），帶了就要是對的；造型的兩個路徑一定要帶
        owner = owner_of(data.get("key"))
        if owner is None and (data.get("key") is not None
                              or path not in ("/api/records", "/api/glyphs", "/api/snapshots", "/api/reports")):
            self._error(400, "key 沒給或格式不對")
            return
        if path == "/api/records":
            records = data.get("records")
            if not isinstance(records, list) or not 0 < len(records) <= MAX_BATCH:
                self._error(400, f"records 要是 1 到 {MAX_BATCH} 筆的陣列")
                return
            try:
                accepted, rejected, uncounted = self.store.add(records, ip, owner=owner)
            except Full as err:
                self._error(507, str(err))
                return
            self._json(200, {"accepted": accepted, "rejected": rejected, "uncounted": uncounted})
        elif path == "/api/glyphs":
            glyphs = data.get("glyphs")
            if not isinstance(glyphs, list) or not 0 < len(glyphs) <= MAX_GLYPHS:
                self._error(400, f"glyphs 要是 1 到 {MAX_GLYPHS} 個的陣列")
                return
            try:
                accepted, rejected = self.store.add_glyphs(glyphs, ip, owner=owner)
            except Full as err:
                self._error(507, str(err))
                return
            self._json(200, {"accepted": accepted, "rejected": rejected})
        elif path in ("/api/snapshots", "/api/reports"):
            report = path == "/api/reports"
            try:
                snap = validate_snapshot(data, report=report)
            except Invalid as err:
                self._error(400, str(err))
                return
            try:
                code = self.store.add_snapshot(snap, owner or f"ip:{ip}", ip)
            except Busy as err:
                self._error(429, str(err))
                return
            except Full as err:
                self._error(507, str(err))
                return
            if not report:
                self._json(200, {"code": code})
                return
            url = self.discord()
            if url:
                threading.Thread(target=relay_to_discord, args=(url, code, snap, data["state"]), daemon=True).start()
            self._json(200, {"code": code, "relayed": bool(url)})
        elif path == "/api/looks/mine":
            self._json(200, {"characters": self.store.my_looks(owner)})
        else:
            try:
                character = _text(data, "character", 24, required=True)
                look = None if data.get("look") is None else validate_look(data["look"])
            except Invalid as err:
                self._error(400, str(err))
                return
            try:
                done = self.store.set_look(owner, character, look, ip)
            except Full as err:
                self._error(507, str(err))
                return
            if not done:
                self._error(403, "這個瀏覽器沒有上傳過這個角色的紀錄")
                return
            self._json(200, {"ok": True})

    def do_DELETE(self) -> None:
        if self._guard() is None:
            return
        prefix = "/api/records/"
        path = urlsplit(self.path).path
        uid = path[len(prefix):] if path.startswith(prefix) else ""
        if not UID.match(uid):
            self._error(404, "沒有這個路徑")
            return
        # 同上：舊版網頁的單筆刪除，不刪，照樣回 204
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
    parser.add_argument("--names", default=str(NAMES_PATH), help="合法職業與地圖清單（scripts/export_server_names.py 產生）")
    args = parser.parse_args(argv)
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    store = Store(args.db, names=Names.load(args.names))
    server = make_server(args.host, args.port, store)
    print(f"Discord 轉發：{'有設' if discord_webhook() else '沒設'}", flush=True)
    print(f"listening on http://{args.host}:{args.port}  db={args.db}  records={store.count()} counted={store.count(counted=True)}", flush=True)
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
