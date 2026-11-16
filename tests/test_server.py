"""後端（server/app.py）：紀錄的檢查、存取、排行榜，以及實際走一趟 HTTP。"""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
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
            {"activeSec": -1}, {"activeSec": 31 * 86400}, {"idleSec": None},
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

    def test_delete(self):
        self.store.add([record(1), record(2)], "::1", NOW)
        self.assertTrue(self.store.delete(uid(1)))
        self.assertFalse(self.store.delete(uid(1)))
        self.assertEqual(self.store.count(), 1)

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
            # 小華：總經驗最多，但效率最高的那段只打了 5 分鐘，不算進效率榜
            record(3, character="小華", job="祭司", level=65, cumNet=900_000, activeSec=7200,
                   startAt=NOW - 2 * HOUR, mapName="天空之塔"),
            record(4, character="小華", job="", level=None, cumNet=200_000, activeSec=300,
                   startAt=NOW - 400 * HOUR, endAt=NOW - 399 * HOUR),
        ], "::1", NOW)

    def test_exp_board_sums_per_character(self):
        board = self.store.boards(now_ms=NOW)["exp"]
        self.assertEqual([(r["character"], r["exp"], r["records"]) for r in board],
                         [("小華", 1_100_000, 2), ("阿明", 460_000, 2)])
        # 職業取最近一筆有讀到的，等級取最高的
        self.assertEqual([(r["job"], r["level"]) for r in board], [("祭司", 65), ("龍騎士", 70)])
        self.assertEqual(board[0]["activeSec"], 7500)

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
        self.store.delete(uid(9))
        self.assertEqual(len(self.store.boards()["exp"]), 2)


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

    def test_upload_then_board_then_delete(self):
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
        self.assertEqual(self.call("DELETE", f"/api/records/{uid(1)}")[0], 204)
        self.assertEqual(self.call("DELETE", f"/api/records/{uid(1)}")[0], 204)
        self.assertEqual(self.store.count(), 0)

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
        self.assertEqual(self.call("POST", "/api/records", b"x" * (app.MAX_BODY + 1))[0], 413)
        self.assertEqual(self.call("POST", "/api/nope", {"records": [record(1)]})[0], 404)
        self.assertEqual(self.call("GET", "/api/leaderboard?days=abc")[0], 400)
        self.assertEqual(self.call("GET", "/api/leaderboard?limit=100000")[0], 400)
        self.assertEqual(self.call("GET", "/nope")[0], 404)
        self.assertEqual(self.call("DELETE", "/api/records/not-a-uid")[0], 404)
        self.assertEqual(self.store.count(), 0)

    def test_page_and_rate_limit(self):
        status, headers, raw = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("排行榜", raw.decode("utf-8"))
        self.server.RequestHandlerClass.limiter = app.RateLimiter(limit=1)
        self.assertEqual(self.call("GET", "/api/health")[0], 200)
        self.assertEqual(self.call("GET", "/api/health")[0], 429)


if __name__ == "__main__":
    unittest.main()
