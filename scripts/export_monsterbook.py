"""從本機的遊戲客戶端匯出怪物圖鑑快照 ``web/monsterbook.json``。

網頁的「怪物圖鑑」分頁讀這份：怪物數值、圖鑑卡上的掉落物與出現地圖、各種名稱。
瀏覽器讀不到客戶端的 bundle，所以跟 ``export_mapvocab.py`` 一樣先在本機匯出。
客戶端改版（加怪、改數值）就重跑一次。格式見 ``mapleexp/monsterbook.py``。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mapleexp import gamedata, monsterbook  # noqa: E402

OUT = ROOT / "web" / "monsterbook.json"


def main() -> int:
    directory = gamedata.find_bundle_dir()
    if directory is None:
        print("找不到遊戲客戶端（沒裝在預設位置，或設 MAPLEEXP_GAME_DIR）", file=sys.stderr)
        return 1
    data = monsterbook.load(directory)
    if not data:
        print("讀不到客戶端的怪物資料（沒有安裝 lz4，或客戶端格式變了）", file=sys.stderr)
        return 1
    monsterbook.dump(data, OUT)
    with_card = sum(1 for m in data["mobs"] if m["drops"] or m["maps"])
    print(
        f"{OUT.relative_to(ROOT)}: {len(data['mobs'])} 隻怪物（{with_card} 隻有圖鑑卡）、"
        f"{len(data['items'])} 種掉落物、{len(data['maps'])} 張地圖，{OUT.stat().st_size / 1024:.0f} KB"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
