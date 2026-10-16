"""從本機的遊戲客戶端匯出地圖清單快照 ``web/mapvocab.json``。

瀏覽器讀不到客戶端的 bundle，所以網頁版只能靠這份快照校正 OCR 讀到的地圖名。
客戶端改版加了新地圖，就要重跑一次 —— 清單裡沒有的地圖，``mapvocab.identify``
一定會硬挑一張最接近的「別張圖」（實測天空之城的紅光庭園 II 被認成公會本部）。

格式是 ``[[map_id, 地區, 地圖名], ...]``，``WebSession.set_map_vocab`` 照這個順序讀。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mapleexp import gamedata  # noqa: E402

OUT = ROOT / "web" / "mapvocab.json"


def main() -> int:
    vocab = gamedata.sync()
    if not vocab:
        print("讀不到客戶端的地圖清單（遊戲沒裝在預設位置，或沒有安裝 lz4）", file=sys.stderr)
        return 1
    rows = [[r.map_id, r.street, r.name] for r in vocab.records]
    OUT.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: {len(rows)} 張地圖（{vocab.source}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
