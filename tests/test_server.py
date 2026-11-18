"""後端（server/app.py）：紀錄的檢查、存取、排行榜，以及實際走一趟 HTTP。"""

from __future__ import annotations

import http.client
import json
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import app  # noqa: E402

NOW = 1_800_000_000_000
HOUR = 3_600_000


def uid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012x}"


def record(n: int, **over) -> dict:
    """網頁會上傳的一筆紀錄（camelCase），預設是一小時打 36 萬經驗。"""
    base = {
        "uid": uid(n), "character": "阿明", "job": "龍騎士", "level": 70,
        "mapId": "abc123", "mapName": "死亡森林", "startAt": NOW - HOUR, "endAt": NOW,
        "cumNet": 360_000, "activeSec": 3600, "idleSec": 0,
    }
    base.update(over)
    return base


class ValidateTest(unittest.TestCase):
    def test_good_record_maps_to_columns(self):
        row = app.validate(record(1), NOW)
        self.assertEqual(row["uid"], uid(1))
        self.assertEqual((row["character"], row["job"], row["level"]), ("阿明", "龍騎士", 70))
        self.assertEqual((row["map_id"], row["map_name"]), ("abc123", "死亡森林"))
        self.assertEqual((row["cum_net"], row["active_sec"], row["idle_sec"]), (360_000, 3600.0, 0.0))

    def test_missing_optional_fields(self):
        row = app.validate(record(1, job=None, level=None, mapId="", mapName=None), NOW)
        self.assertEqual((row["job"], row["level"], row["map_id"], row["map_name"]), ("", None, "", ""))

    def test_rejects_bad_values(self):
        bad = [
            {"uid": "abc"}, {"uid": "ABCDEF12-0000-4000-8000-000000000001"}, {"uid": uid(1) + "\n"}, {"uid": None},
            {"character": ""}, {"character": "   "}, {"character": "x" * 25}, {"character": "a\nb"}, {"character": 5},
            {"level": 0}, {"level": 301}, {"level": 70.5}, {"level": "70"}, {"level": True},
            {"cumNet": 0}, {"cumNet": -5}, {"cumNet": 1e14}, {"cumNet": "9"}, {"cumNet": float("nan")},
            {"activeSec": -1}, {"activeSec": 599}, {"activeSec": 31 * 86400}, {"idleSec": None},
            {"startAt": 0}, {"endAt": NOW - 2 * HOUR}, {"endAt": NOW + 2 * app.DAY_MS},
            {"mapName": "x" * 49},
        ]
        for over in bad:
            with self.subTest(over=over), self.assertRaises(app.Invalid):
                app.validate(record(1, **over), NOW)
        for raw in (None, [], "x", 3):
            with self.subTest(raw=raw), self.assertRaises(app.Invalid):
                app.validate(raw, NOW)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.store = app.Store(":memory:")
        self.addCleanup(self.store.close)

    def rows(self):
        return self.store._db.execute("SELECT uid, character, ip, received_at FROM records ORDER BY uid").fetchall()

    def test_add_keeps_ip_and_time(self):
        accepted, rejected = self.store.add([record(1)], "203.0.113.9", NOW)
        self.assertEqual((accepted, rejected), ([uid(1)], []))
        self.assertEqual(self.rows(), [(uid(1), "阿明", "203.0.113.9", NOW)])

    def test_same_uid_twice_is_one_record(self):
        self.store.add([record(1)], "203.0.113.9", NOW)
        # 重傳（上次的回應沒收到）：照樣回收下，但不多一筆、也不蓋掉原本的
        accepted, _ = self.store.add([record(1, cumNet=999, character="別人")], "198.51.100.1", NOW + 5)
        self.assertEqual(accepted, [uid(1)])
        self.assertEqual(self.rows(), [(uid(1), "阿明", "203.0.113.9", NOW)])

    def test_bad_ones_are_reported_good_ones_kept(self):
        accepted, rejected = self.store.add([record(1), record(2, cumNet=-1), "x", record(3)], "::1", NOW)
        self.assertEqual(accepted, [uid(1), uid(3)])
        self.assertEqual([r["uid"] for r in rejected], [uid(2), None])
        self.assertTrue(all(r["reason"] for r in rejected))
        self.assertEqual(self.store.count(), 2)

    def test_short_records_kept_before_the_minimum_are_dropped_on_start(self):
        import tempfile

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "r.db"
        store = app.Store(path)
        store.add([record(1), record(2)], "::1", NOW)
        # 門檻加上去以前收下的短紀錄：現在的 add 存不進這種的，直接改資料表模擬
        store._db.execute("UPDATE records SET active_sec = 599 WHERE uid = ?", (uid(2),))
        store._db.commit()
        store.close()
        store = app.Store(path)
        self.addCleanup(store.close)
        self.assertEqual(store._db.execute("SELECT uid FROM records").fetchall(), [(uid(1),)])

    def test_full_store_refuses(self):
        import tempfile

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        store = app.Store(Path(folder.name) / "r.db", max_bytes=0)
        self.addCleanup(store.close)
        with self.assertRaises(app.Full):
            store.add([record(1)], "::1", NOW)
        self.assertEqual(store.count(), 0)


class BoardsTest(unittest.TestCase):
    def setUp(self):
        self.store = app.Store(":memory:")
        self.addCleanup(self.store.close)
        self.store.add([
            # 阿明：兩段，後來轉職升等；第二段效率比較高
            record(1, startAt=NOW - 50 * HOUR, endAt=NOW - 49 * HOUR, job="槍騎兵", level=68, cumNet=100_000),
            record(2, job="龍騎士", level=70, cumNet=360_000, mapName="死亡森林"),
            # 小華：總經驗最多；另一段只打了 5 分鐘，不滿 10 分鐘的不收，哪個榜都不會算到
            record(3, character="小華", job="祭司", level=65, cumNet=900_000, activeSec=7200,
                   startAt=NOW - 2 * HOUR, mapName="天空之塔"),
            record(4, character="小華", job="", level=None, cumNet=200_000, activeSec=300,
                   startAt=NOW - 400 * HOUR, endAt=NOW - 399 * HOUR),
        ], "::1", NOW)

    def test_exp_board_sums_per_character(self):
        board = self.store.boards(now_ms=NOW)["exp"]
        self.assertEqual([(r["character"], r["exp"], r["records"]) for r in board],
                         [("小華", 900_000, 1), ("阿明", 460_000, 2)])
        # 職業取最近一筆有讀到的，等級取最高的
        self.assertEqual([(r["job"], r["level"]) for r in board], [("祭司", 65), ("龍騎士", 70)])
        self.assertEqual(board[0]["activeSec"], 7200)

    def test_rate_board_takes_best_long_enough_record(self):
        board = self.store.boards(now_ms=NOW)["rate"]
        self.assertEqual([(r["character"], r["rate"]) for r in board], [("小華", 450_000), ("阿明", 360_000)])
        # 等級、地圖是創下紀錄的那一段的
        self.assertEqual((board[1]["level"], board[1]["mapName"], board[1]["job"]), (70, "死亡森林", "龍騎士"))

    def test_level_board(self):
        board = self.store.boards(now_ms=NOW)["level"]
        self.assertEqual([(r["character"], r["level"], r["job"]) for r in board],
                         [("阿明", 70, "龍騎士"), ("小華", 65, "祭司")])

    def test_days_window_and_limit(self):
        recent = self.store.boards(days=1, now_ms=NOW)
        self.assertEqual([(r["character"], r["exp"]) for r in recent["exp"]], [("小華", 900_000), ("阿明", 360_000)])
        self.assertEqual(len(self.store.boards(limit=1, now_ms=NOW)["exp"]), 1)

    def test_board_never_leaks_uid_or_ip(self):
        text = json.dumps(self.store.boards(now_ms=NOW))
        self.assertNotIn(uid(1), text)
        self.assertNotIn("::1", text)

    def test_cached_board_refreshes_after_write(self):
        self.assertEqual(len(self.store.boards()["exp"]), 2)
        self.store.add([record(9, character="新人")], "::1", NOW)
        self.assertEqual(len(self.store.boards()["exp"]), 3)


class TrainingTest(unittest.TestCase):
    def setUp(self):
        self.store = app.Store(":memory:")
        self.addCleanup(self.store.close)
        self.store.add([
            # 死亡森林、龍騎士、61–70：兩個人各一小時，合起來算
            record(1, cumNet=360_000),
            record(2, character="小美", level=65, cumNet=180_000),
            # 同一張圖同一個職業，升到 71 就是另一個區間
            record(3, level=71, cumNet=400_000),
            # 天空之塔、祭司、61–70：最近一段效率高，很久以前那段拉低了全期的平均
            record(4, character="小華", job="祭司", level=65, mapName="天空之塔", cumNet=900_000, activeSec=7200,
                   startAt=NOW - 2 * HOUR),
            record(5, character="小華", job="祭司", level=62, mapName="天空之塔", cumNet=100_000,
                   startAt=NOW - 400 * HOUR, endAt=NOW - 399 * HOUR),
            # 活躍不到 10 分鐘的根本不收；沒讀到地圖、職業、等級的不算
            record(6, character="路人", job="法師", level=30, mapName="姑姑鐘", cumNet=9_000_000, activeSec=300),
            record(7, mapName=""), record(8, job=""), record(9, level=None),
            record(10, character="路人", job="法師", level=30, mapName="姑姑鐘", cumNet=30_000, activeSec=600),
        ], "::1", NOW)

    def rows(self, **kw):
        return [(r["mapName"], r["job"], r["levelFrom"], r["levelTo"], r["rate"])
                for r in self.store.training(now_ms=NOW, **kw)["rows"]]

    def test_groups_by_map_job_and_level_band(self):
        out = self.store.training(now_ms=NOW)
        self.assertEqual(self.rows(), [
            ("死亡森林", "龍騎士", 71, 80, 400_000),
            ("天空之塔", "祭司", 61, 70, 333_333),
            ("死亡森林", "龍騎士", 61, 70, 270_000),
            ("姑姑鐘", "法師", 21, 30, 180_000),
        ])
        low = out["rows"][2]
        self.assertEqual((low["exp"], low["activeSec"], low["records"], low["characters"]), (540_000, 7200, 2, 2))
        # 篩選用的選項：這段期間有資料的職業與區間起點
        self.assertEqual((out["jobs"], out["levels"]), (["法師", "祭司", "龍騎士"], [21, 61, 71]))

    def test_filters_and_window(self):
        self.assertEqual([r[4] for r in self.rows(job="龍騎士")], [400_000, 270_000])
        self.assertEqual([r[:2] for r in self.rows(level=65)], [("天空之塔", "祭司"), ("死亡森林", "龍騎士")])
        self.assertEqual(self.rows(job="祭司", level=70, days=1), [("天空之塔", "祭司", 61, 70, 450_000)])
        self.assertEqual(self.rows(job="沒這個職業"), [])
        self.assertEqual(len(self.rows(limit=1)), 1)

    def test_never_leaks_uid_ip_or_names(self):
        text = json.dumps(self.store.training(now_ms=NOW), ensure_ascii=False)
        for secret in (uid(1), "::1", "阿明", "小華"):
            self.assertNotIn(secret, text)

    def test_cached_until_a_write(self):
        self.assertEqual(len(self.store.training()["rows"]), 4)
        self.store.add([record(20, mapName="新地圖")], "::1", NOW)
        self.assertEqual(len(self.store.training()["rows"]), 5)


def key(n: int) -> str:
    """第 n 個瀏覽器的金鑰。"""
    return f"11111111-1111-4111-8111-{n:012x}"


LOOK = {"skin": 1, "hair": 30027, "face": 20100, "mix": {"hair": 3, "face": None},
        "worn": {"cap": 1002140, "weapon": 1702000}, "group": 30}


class LooksTest(unittest.TestCase):
    def setUp(self):
        self.store = app.Store(":memory:")
        self.addCleanup(self.store.close)
        self.a, self.b = app.owner_of(key(1)), app.owner_of(key(2))

    def owners(self):
        return dict(self.store._db.execute("SELECT uid, owner FROM records").fetchall())

    def test_look_is_checked_and_trimmed_to_known_fields(self):
        self.assertEqual(app.validate_look({**LOOK, "extra": "x", "pic": "data:image/png;base64,AAAA"}), LOOK)
        # 沒寫的混色、部位、武器類型就是沒有
        self.assertEqual(app.validate_look({"skin": 0, "hair": 30000, "face": 20000}),
                         {"skin": 0, "hair": 30000, "face": 20000, "mix": {"hair": None, "face": None}, "worn": {}, "group": None})
        bad = [
            None, [], "x", {}, {**LOOK, "skin": -1}, {**LOOK, "skin": "1"}, {**LOOK, "skin": True}, {**LOOK, "hair": None},
            {**LOOK, "face": 1.5}, {**LOOK, "hair": 10 ** 8}, {**LOOK, "mix": []}, {**LOOK, "mix": {"hair": "3"}},
            {**LOOK, "worn": []}, {**LOOK, "worn": {"cap": "1002140"}}, {**LOOK, "worn": {"cap": None}},
            {**LOOK, "worn": {"<b>": 1}}, {**LOOK, "worn": {"": 1}}, {**LOOK, "worn": {"x" * 25: 1}},
            {**LOOK, "worn": {f"part{chr(97 + n)}": 1 for n in range(app.LOOK_PARTS + 1)}}, {**LOOK, "group": "30"},
        ]
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(app.Invalid):
                app.validate_look(raw)

    def test_key_is_stored_hashed(self):
        self.assertRegex(self.a, r"^[0-9a-f]{64}$")
        self.assertNotEqual(self.a, self.b)
        self.assertNotIn(key(1), self.a)
        for raw in (None, "", "abc", 5, key(10).upper(), key(1) + "x"):      # key(10) 的尾巴是 a，大寫就不合格
            self.assertIsNone(app.owner_of(raw))

    def test_first_uploader_owns_a_record_and_old_records_are_claimed_on_resend(self):
        self.store.add([record(1)], "::1", NOW)                    # 這個功能上線前收的：沒有主
        self.store.add([record(2)], "::1", NOW, owner=self.a)
        self.assertEqual(self.owners(), {uid(1): None, uid(2): self.a})
        # 網頁每次開頁都重傳：沒主的歸重傳的人，已經有主的不改
        self.store.add([record(1), record(2)], "::1", NOW, owner=self.b)
        self.assertEqual(self.owners(), {uid(1): self.b, uid(2): self.a})
        self.assertEqual(self.store.count(), 2)

    def test_only_a_browser_with_records_of_the_character_can_set_its_look(self):
        self.store.add([record(1)], "::1", NOW, owner=self.a)
        self.assertFalse(self.store.set_look(self.b, "阿明", LOOK, "::1", NOW))
        self.assertFalse(self.store.set_look(self.a, "別人", LOOK, "::1", NOW))
        self.assertEqual(self.store.my_looks(self.a), [{"character": "阿明", "look": None}])
        self.assertTrue(self.store.set_look(self.a, "阿明", LOOK, "203.0.113.9", NOW))
        self.assertEqual(self.store.my_looks(self.a), [{"character": "阿明", "look": LOOK}])
        self.assertEqual(self.store.my_looks(self.b), [])
        self.assertEqual(self.store._db.execute("SELECT owner, ip, updated_at FROM looks").fetchall(),
                         [(self.a, "203.0.113.9", NOW)])

    def test_two_browsers_with_the_same_character_can_both_change_it(self):
        self.store.add([record(1)], "::1", NOW, owner=self.a)
        self.store.add([record(2)], "::1", NOW, owner=self.b)
        self.store.set_look(self.a, "阿明", LOOK, "::1", NOW)
        other = {**LOOK, "hair": 30030, "worn": {}}
        self.assertTrue(self.store.set_look(self.b, "阿明", other, "::1", NOW + 1))
        # 後設的蓋掉先設的，兩邊看到的都是現在這一套
        self.assertEqual(self.store.my_looks(self.a), [{"character": "阿明", "look": other}])
        self.assertTrue(self.store.set_look(self.a, "阿明", None, "::1", NOW + 2))
        self.assertEqual(self.store.my_looks(self.b), [{"character": "阿明", "look": None}])
        self.assertEqual(self.store._db.execute("SELECT COUNT(*) FROM looks").fetchone()[0], 0)

    def test_board_carries_the_looks_of_the_characters_listed(self):
        names = ["甲", "乙", "丙", "丁"]
        self.store.add([record(n + 1, character=name, cumNet=1_000_000 - n * 1000, level=90 - n)
                        for n, name in enumerate(names)], "::1", NOW, owner=self.a)
        self.assertEqual(self.store.boards(now_ms=NOW)["looks"], {})
        for name in ("乙", "丁"):
            self.store.set_look(self.a, name, {**LOOK, "skin": names.index(name)}, "::1", NOW)
        # 榜上的人有設的都附上（頒獎台畫全身、名單畫頭像）；設完造型快取也要跟著更新
        self.assertEqual(self.store.boards()["looks"], {"乙": {**LOOK, "skin": 1}, "丁": {**LOOK, "skin": 3}})
        # 沒擠進這次回應的人（limit 之外）不送
        self.assertEqual(self.store.boards(limit=3, now_ms=NOW)["looks"], {"乙": {**LOOK, "skin": 1}})
        text = json.dumps(self.store.boards(now_ms=NOW))
        self.assertNotIn(self.a, text)
        self.assertNotIn(key(1), text)

    def test_database_from_before_gets_the_new_column(self):
        import sqlite3
        import tempfile

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "old.db"
        old = sqlite3.connect(path)
        # v0.34 的資料表：沒有 owner 欄、沒有 looks 表
        old.executescript(app.SCHEMA.split("CREATE TABLE IF NOT EXISTS looks")[0].replace(",\n    owner       TEXT", ""))
        old.execute("INSERT INTO records VALUES (?, '阿明', '龍騎士', 70, 'm', '死亡森林', ?, ?, 360000, 3600, 0, '::1', ?)",
                    (uid(1), NOW - HOUR, NOW, NOW))
        old.commit()
        old.close()
        store = app.Store(path)
        self.addCleanup(store.close)
        self.assertEqual(store._db.execute("SELECT uid, owner FROM records").fetchall(), [(uid(1), None)])
        store.add([record(1)], "::1", NOW, owner=self.a)
        self.assertTrue(store.set_look(self.a, "阿明", LOOK, "::1", NOW))


class LimiterAndIpTest(unittest.TestCase):
    def test_rate_limit_per_ip_and_window(self):
        limiter = app.RateLimiter(limit=2, window=60)
        self.assertEqual([limiter.allow("a", 0), limiter.allow("a", 1), limiter.allow("a", 2)], [True, True, False])
        self.assertTrue(limiter.allow("b", 2))
        self.assertTrue(limiter.allow("a", 61))

    def test_forwarded_header_only_trusted_from_loopback(self):
        headers = {"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "198.51.100.2, 10.0.0.1"}
        self.assertEqual(app.client_ip("127.0.0.1", headers), "203.0.113.7")
        self.assertEqual(app.client_ip("::1", {"X-Forwarded-For": "198.51.100.2, 10.0.0.1"}), "198.51.100.2")
        self.assertEqual(app.client_ip("192.168.1.5", headers), "192.168.1.5")
        self.assertEqual(app.client_ip("127.0.0.1", {"CF-Connecting-IP": "not-an-ip"}), "127.0.0.1")
        self.assertEqual(app.client_ip("127.0.0.1", {}), "127.0.0.1")


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.store = app.Store(":memory:")
        self.server = app.make_server("127.0.0.1", 0, self.store, app.RateLimiter(limit=1000))
        # 出錯的請求會寫 log，測試裡故意送一堆壞請求，別洗版
        self.server.RequestHandlerClass.log_message = lambda *args: None
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

        def stop():
            self.server.shutdown()
            self.server.server_close()
            thread.join(5)
            self.store.close()
        self.addCleanup(stop)

    def call(self, method: str, path: str, body=None, headers=None):
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                raw = res.read()
                return res.status, dict(res.headers), raw
        except urllib.error.HTTPError as err:
            with err:
                return err.code, dict(err.headers), err.read()

    def test_upload_then_board(self):
        now = app.time.time() * 1000
        rec = record(1, startAt=now - HOUR, endAt=now)
        status, headers, raw = self.call("POST", "/api/records", {"records": [rec, record(2, cumNet=0)]},
                                         {"Content-Type": "application/json", "X-Forwarded-For": "203.0.113.50"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")
        out = json.loads(raw)
        self.assertEqual(out["accepted"], [uid(1)])
        self.assertEqual(out["rejected"][0]["uid"], uid(2))
        # 測試是從本機連的，所以轉送標頭裡的位址算數
        self.assertEqual(self.store._db.execute("SELECT ip FROM records").fetchall(), [("203.0.113.50",)])

        status, _, raw = self.call("GET", "/api/leaderboard?days=7&limit=5")
        self.assertEqual(status, 200)
        board = json.loads(raw)
        self.assertEqual(board["exp"][0]["character"], "阿明")
        self.assertNotIn(uid(1), raw.decode("utf-8"))
        self.assertNotIn("203.0.113.50", raw.decode("utf-8"))

        self.assertEqual(json.loads(self.call("GET", "/api/health")[2]), {"ok": True, "records": 1})

    def test_old_pages_cannot_delete_any_more(self):
        # 收下的紀錄不刪。舊版網頁還會來叫刪除，照樣回 204（不然它會卡著一直重試、後面的也傳不上來），但什麼都不動
        now = app.time.time() * 1000
        self.call("POST", "/api/records", {"records": [record(1, startAt=now - HOUR, endAt=now)]},
                  {"Content-Type": "application/json"})
        self.assertEqual(self.call("DELETE", f"/api/records/{uid(1)}")[0], 204)
        status, _headers, raw = self.call("POST", "/api/records/delete", {"uids": [uid(1), "not-a-uid"]},
                                          {"Content-Type": "application/json"})
        self.assertEqual((status, raw), (204, b""))
        self.assertEqual(self.store.count(), 1)

    def test_resend_is_ignored(self):
        # 網頁每次開啟都整批重傳：同樣的 uid 再來幾次都只有一筆，回應照樣說收下了
        now = app.time.time() * 1000
        batch = {"records": [record(n, startAt=now - HOUR, endAt=now) for n in (1, 2, 3)]}
        for _ in range(3):
            status, _headers, raw = self.call("POST", "/api/records", batch, {"Content-Type": "application/json"})
            self.assertEqual((status, json.loads(raw)["accepted"]), (200, [uid(1), uid(2), uid(3)]))
        self.assertEqual(self.store.count(), 3)

    def test_looks_over_http(self):
        now = app.time.time() * 1000
        post = lambda path, body: self.call("POST", path, body, {"Content-Type": "application/json"})   # noqa: E731
        recs = [record(1, startAt=now - HOUR, endAt=now)]
        self.assertEqual(post("/api/records", {"key": key(1), "records": recs})[0], 200)
        # 別的瀏覽器重傳同一筆、或沒傳過這個角色：都不能設
        self.assertEqual(post("/api/records", {"key": key(2), "records": recs})[0], 200)
        status, _headers, raw = post("/api/looks", {"key": key(2), "character": "阿明", "look": LOOK})
        self.assertEqual((status, "沒有上傳過" in raw.decode("utf-8")), (403, True))
        self.assertEqual(json.loads(post("/api/looks/mine", {"key": key(2)})[2]), {"characters": []})

        self.assertEqual(json.loads(post("/api/looks", {"key": key(1), "character": " 阿明 ", "look": {**LOOK, "junk": 1}})[2]), {"ok": True})
        self.assertEqual(json.loads(post("/api/looks/mine", {"key": key(1)})[2]), {"characters": [{"character": "阿明", "look": LOOK}]})
        raw = self.call("GET", "/api/leaderboard")[2].decode("utf-8")
        self.assertEqual(json.loads(raw)["looks"], {"阿明": LOOK})
        for secret in (key(1), app.owner_of(key(1)), uid(1)):
            self.assertNotIn(secret, raw)

        self.assertEqual(post("/api/looks", {"key": key(1), "character": "阿明", "look": None})[0], 200)
        self.assertEqual(json.loads(self.call("GET", "/api/leaderboard")[2])["looks"], {})

        bad = [
            ("/api/looks", {"character": "阿明", "look": LOOK}), ("/api/looks", {"key": "nope", "character": "阿明", "look": LOOK}),
            ("/api/looks", {"key": key(1), "look": LOOK}), ("/api/looks", {"key": key(1), "character": "x" * 25, "look": LOOK}),
            ("/api/looks", {"key": key(1), "character": "阿明", "look": {"skin": "x"}}), ("/api/looks", [1]),
            ("/api/looks/mine", {}), ("/api/looks/mine", {"key": 5}), ("/api/records", {"key": "nope", "records": recs}),
        ]
        for path, body in bad:
            self.assertEqual(post(path, body)[0], 400, (path, body))
        # 舊版網頁上傳不帶金鑰，照收
        self.assertEqual(post("/api/records", {"records": [record(3, startAt=now - HOUR, endAt=now)]})[0], 200)

    def test_preflight(self):
        status, headers, _ = self.call("OPTIONS", "/api/records", headers={
            "Origin": "https://andy971029.github.io", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type"})
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")
        self.assertIn("DELETE", headers["Access-Control-Allow-Methods"])
        self.assertIn("Content-Type", headers["Access-Control-Allow-Headers"])

    def test_bad_requests(self):
        self.assertEqual(self.call("POST", "/api/records", b"{not json")[0], 400)
        self.assertEqual(self.call("POST", "/api/records", {"records": []})[0], 400)
        self.assertEqual(self.call("POST", "/api/records", {"records": [record(1)] * (app.MAX_BATCH + 1)})[0], 400)
        self.assertEqual(self.call("POST", "/api/records", ["x"])[0], 400)
        # 太大的內容後端不讀、回 413 就斷線；用戶端有時還在送就被斷，連回應都收不到，兩種都算擋下來了
        try:
            self.assertEqual(self.call("POST", "/api/records", b"x" * (app.MAX_BODY + 1))[0], 413)
        except OSError:
            pass
        self.assertEqual(self.store.count(), 0)
        self.assertEqual(self.call("POST", "/api/nope", {"records": [record(1)]})[0], 404)
        self.assertEqual(self.call("GET", "/api/leaderboard?days=abc")[0], 400)
        self.assertEqual(self.call("GET", "/api/leaderboard?limit=100000")[0], 400)
        self.assertEqual(self.call("GET", "/nope")[0], 404)
        self.assertEqual(self.call("DELETE", "/api/records/not-a-uid")[0], 404)
        self.assertEqual(self.store.count(), 0)

    def test_training_over_http(self):
        now = app.time.time() * 1000
        recs = [record(1, startAt=now - HOUR, endAt=now),
                record(2, character="小華", job="祭司", level=35, mapName="天空之塔", startAt=now - HOUR, endAt=now)]
        self.call("POST", "/api/records", {"records": recs}, {"Content-Type": "application/json"})
        query = urllib.parse.urlencode({"job": "祭司", "level": 31, "days": 7, "limit": 5})
        status, _headers, raw = self.call("GET", "/api/training?" + query)
        out = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual([(r["mapName"], r["job"], r["levelFrom"], r["levelTo"]) for r in out["rows"]],
                         [("天空之塔", "祭司", 31, 40)])
        self.assertEqual((out["jobs"], out["levels"]), (["祭司", "龍騎士"], [31, 61]))
        self.assertEqual(len(json.loads(self.call("GET", "/api/training")[2])["rows"]), 2)
        for bad in ("level=0", "level=301", "level=x", "days=0", "limit=100000", "job=" + "x" * 25):
            self.assertEqual(self.call("GET", "/api/training?" + bad)[0], 400, bad)

    def test_root_redirects_to_the_site_and_rate_limit(self):
        # 畫面在網站上，後端自己沒有頁面；urllib 會自動跟著轉址跑去外面，所以這裡自己發請求
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        self.addCleanup(conn.close)
        conn.request("GET", "/")
        res = conn.getresponse()
        self.assertEqual((res.status, res.getheader("Location")), (302, app.SITE + "#rank"))
        res.read()
        self.server.RequestHandlerClass.limiter = app.RateLimiter(limit=1)
        self.assertEqual(self.call("GET", "/api/health")[0], 200)
        self.assertEqual(self.call("GET", "/api/health")[0], 429)


if __name__ == "__main__":
    unittest.main()
