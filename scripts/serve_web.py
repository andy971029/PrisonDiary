"""網頁版原型的本機伺服器。

標準的 ``http.server`` 只送 Last-Modified，Chrome 會靠啟發式快取，改了 bridge.py
或重打包 mapleexp.zip 之後一般重新整理仍可能拿到舊檔，看起來就像「修了沒效」。
開發原型時每次都要拿最新的，所以一律送 no-store。

紙娃娃的資料是幾十個大包，網頁用 Range 只抓一件的那一段（GitHub Pages 支援），這裡也要支援，
不然每選一件都整包下載。只處理單一區段 ``bytes=起-迄``，其他照舊整檔送。
"""

from __future__ import annotations

import os
import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
PORT = 8765


RANGE = re.compile(r"bytes=(\d+)-(\d*)$")


class NoStoreHandler(SimpleHTTPRequestHandler):
    length: int | None = None

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_head(self):
        match = RANGE.match(self.headers.get("Range", ""))
        path = self.translate_path(self.path)
        if not match or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        start = int(match[1])
        end = min(int(match[2]) if match[2] else size - 1, size - 1)
        if start > end:
            self.send_error(416)
            return None
        handle = open(path, "rb")
        handle.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self.length = end - start + 1
        return handle

    def copyfile(self, source, outputfile) -> None:
        if self.length is None:
            return super().copyfile(source, outputfile)
        left = self.length
        while left > 0:
            chunk = source.read(min(65536, left))
            if not chunk:
                break
            outputfile.write(chunk)
            left -= len(chunk)


def main() -> int:
    handler = partial(NoStoreHandler, directory=str(WEB))
    with ThreadingHTTPServer(("127.0.0.1", PORT), handler) as server:
        print(f"http://localhost:{PORT}/  (Ctrl+C 停止)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
