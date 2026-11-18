"""從本機的遊戲客戶端匯出紙娃娃特效的定位 ``web/avatar-effects.json``。

圖放在 ``Effect/ItemEff`` 的時裝（翅膀、跟在旁邊的小動物、帽子上飄的東西），每個動作各有 ``pos`` 與 ``z``：
原點對在眉心還是腳底、畫在角色前面還是後面。格式是 ``{時裝 ID: {動作: [pos, z]}}``，規則見 ``mapleexp/avatar.py``
的 :func:`effect_meta`。只有幾十件、幾 KB，所以跟紙娃娃那幾十包圖分開放在主站，網頁開紙娃娃時一起載。

只讀數值，不需要 UnityPy；沒有 ``lz4`` 就用純 Python 的解壓。只收 ``web/avatar.json`` 裡有的時裝，所以先跑
``export_avatar.py``（或用現成的 ``avatar.json``）。
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mapleexp import avatar, gamedata, itembook, wzjs  # noqa: E402

OUT = ROOT / "web" / "avatar-effects.json"
CATALOG = ROOT / "web" / "avatar.json"
EFFECT_MARKER = struct.pack("<i", 7) + b"ItemEff\0"      # 跟 export_avatar.py 同一個


def main() -> int:
    if gamedata._lz4 is None:
        from mapleexp import lz4py

        gamedata._lz4 = lz4py
        print("沒有 lz4，改用純 Python 解壓（比較慢）", file=sys.stderr)
    directory = gamedata.find_bundle_dir()
    if directory is None:
        print("找不到遊戲客戶端。請先開著遊戲，或設定 MAPLEEXP_GAME_DIR。", file=sys.stderr)
        return 1
    if not CATALOG.exists():
        print(f"先跑 scripts/export_avatar.py 產生 {CATALOG.relative_to(ROOT)}，才知道要收哪些時裝", file=sys.stderr)
        return 1
    items = {str(item["id"]) for group in json.loads(CATALOG.read_text(encoding="utf-8"))["items"].values() for item in group}
    found = gamedata.find_bundle_containing(directory, EFFECT_MARKER, limit=itembook.EQUIP_BUNDLE_LIMIT)
    tree = next((t for n, t in wzjs.iter_assets(found[1], canvas=True) if n == "ItemEff"), None) if found else None
    if tree is None:
        print("客戶端裡找不到 Effect/ItemEff（格式可能改了）。", file=sys.stderr)
        return 1
    data = {}
    for item in sorted(items & set(tree), key=int):
        meta = avatar.effect_meta(tree, item)
        if meta:
            data[item] = meta
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    actions = [pos for meta in data.values() for pos, _z in meta.values()]
    print(f"{len(data)} 件有特效的時裝、{len(actions)} 個動作（{sum(actions)} 個對眉心，其餘對腳底） -> {OUT}（{OUT.stat().st_size / 1024:.1f} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
