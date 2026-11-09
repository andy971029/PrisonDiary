"""把 ``web/`` 裡的怪物、NPC、小地圖散圖併成幾個 ``.bin`` 包，給部署用。

這三個資料夾加起來兩千多個檔案，GitHub Pages 部署時常卡在 ``syncing_files``，
所以網站上不放散檔：每包約 2 MB，檔名帶內容雜湊（包換了瀏覽器不會拿舊快取），
``packs.json`` 記每張圖在哪包的哪一段，網頁用 Range 只抓那一段。
只在部署時做（``.github/workflows/pages.yml``），repo 裡還是散檔；本機開發沒有 ``packs.json``，網頁就直接讀散檔。

用法：``python scripts/pack_images.py <web 目錄> <輸出目錄>``，輸出 ``<輸出>/packs/*.bin`` 與 ``<輸出>/packs.json``。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

KINDS = ("mobs", "npcs", "minimaps")
LIMIT = 2 << 20


def pack(web: Path, out: Path, kinds: tuple[str, ...] = KINDS, limit: int = LIMIT) -> dict[str, list]:
    """回傳並寫出索引 ``{"mobs/100100.png": [包名, 起點, 長度], ...}``。"""
    index: dict[str, list] = {}
    (out / "packs").mkdir(parents=True, exist_ok=True)

    def flush(kind: str, chunks: list[tuple[str, bytes]]) -> None:
        blob = b"".join(data for _, data in chunks)
        name = f"{kind}-{hashlib.sha1(blob).hexdigest()[:10]}.bin"
        (out / "packs" / name).write_bytes(blob)
        at = 0
        for path, data in chunks:
            index[path] = [name, at, len(data)]
            at += len(data)

    for kind in kinds:
        chunks: list[tuple[str, bytes]] = []
        size = 0
        for file in sorted((web / kind).glob("*.png")):
            data = file.read_bytes()
            if chunks and size + len(data) > limit:
                flush(kind, chunks)
                chunks, size = [], 0
            chunks.append((f"{kind}/{file.name}", data))
            size += len(data)
        if chunks:
            flush(kind, chunks)
    (out / "packs.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    return index


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    index = pack(Path(sys.argv[1]), Path(sys.argv[2]))
    files = len({name for name, _, _ in index.values()})
    print(f"{len(index)} 張圖併成 {files} 包")
    return 0


if __name__ == "__main__":
    sys.exit(main())
