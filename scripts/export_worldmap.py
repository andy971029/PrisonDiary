"""從本機的遊戲客戶端匯出世界地圖：``web/worldmap.json`` 與 ``web/worldmap/`` 的圖。

網頁「圖鑑」的地圖分頁讀這些。格式見 ``mapleexp/worldmap.py``。地圖圖集在 ``spritesheet_*.bundle``，
節點圖示與目前位置在 ``spriteset_*.bundle``，都用 UnityPy 讀。底圖存 WebP（只有 PNG 的四成大），
區域與路線是小圖，要逐像素判斷滑鼠在不在形狀上，存 PNG。客戶端改版就重跑一次。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mapleexp import gamedata, spritesheet, worldbook, worldmap  # noqa: E402

OUT = ROOT / "web" / "worldmap.json"
PIC_DIR = ROOT / "web" / "worldmap"
SHEET_PREFIX = "Assets/WzAssets/SpriteSheet/TW/Map/WorldMap/"
ICON_PREFIX = "Assets/WzAssets/SpriteSet/Common/Map/MapHelper/worldMap/"
WEBP_QUALITY = 88


def load_sheets(directory: Path):
    """回傳 ``({名稱: Sheet}, {檔名: 貼圖的 UnityPy 物件})``；找不到就都是空的。"""
    import UnityPy

    for path in sorted(directory.glob("spritesheet_*.bundle"), key=lambda p: p.stat().st_size):
        env = UnityPy.load(str(path))
        found = {key[len(SHEET_PREFIX):]: obj for key, obj in env.container.items() if key.startswith(SHEET_PREFIX)}
        if found:
            print(f"世界地圖圖集：{path.name}", file=sys.stderr)
            sheets = {name[:-len(".asset")]: spritesheet.parse(obj.deref().get_raw_data())
                      for name, obj in found.items() if name.endswith(".asset")}
            return sheets, {name: obj for name, obj in found.items() if name.endswith(".png")}
    return {}, {}


def sprites_of(raw: bytes, sprites: dict) -> list:
    """SpriteSet 的 MonoBehaviour 沒附 type tree：掃過資料，對得上 Sprite 的 path ID 就是一格，照出現順序。"""
    out = []
    for offset in range(0, len(raw) - 7, 4):
        found = sprites.get(struct.unpack_from("<q", raw, offset)[0])
        if found is not None:
            out.append(found)
    return out


def export_icons(directory: Path) -> int:
    """節點圖示（``mapImage`` 依節點 type 一格）與目前位置（``curPos`` 的第一格）。回傳匯出幾張。"""
    import UnityPy

    for path in sorted(directory.glob("spriteset_*.bundle"), key=lambda p: p.stat().st_size):
        env = UnityPy.load(str(path))
        assets = {key[len(ICON_PREFIX):]: obj for key, obj in env.container.items() if key.startswith(ICON_PREFIX)}
        if "mapImage.asset" not in assets:
            continue
        sprites = {obj.path_id: obj for obj in env.objects if obj.type.name == "Sprite"}
        nodes = sprites_of(assets["mapImage.asset"].deref().get_raw_data(), sprites)
        for kind, sprite in enumerate(nodes):
            sprite.read().image.convert("RGBA").save(PIC_DIR / f"node{kind}.png", optimize=True)
        cur = sprites_of(assets["curPos.asset"].deref().get_raw_data(), sprites) if "curPos.asset" in assets else []
        if cur:
            cur[0].read().image.convert("RGBA").save(PIC_DIR / "cur.png", optimize=True)
        return len(nodes) + bool(cur)
    return 0


def export_pictures(data: dict, sheets: dict, textures: dict) -> None:
    def crop(name: str, frame: spritesheet.Frame):
        page = textures[f"{name}_{frame.page}.png"].deref().read().image.convert("RGBA")
        return page.crop(frame.box(page.height))

    for world in data["worlds"]:
        name, sheet = world["id"], sheets[world["id"]]
        crop(name, sheet.frame(worldmap.BASE_FRAME)).save(PIC_DIR / f"{name}.webp", quality=WEBP_QUALITY, method=6)
        for link in world["links"]:
            crop(name, sheet.frame(worldmap.link_frame(link["i"]))).save(PIC_DIR / f"{name}_link{link['i']}.png", optimize=True)
        for index, node in enumerate(world["nodes"]):
            if "path" in node:
                crop(name, sheet.frame(worldmap.path_frame(index))).save(PIC_DIR / f"{name}_path{index}.png", optimize=True)


def main() -> int:
    directory = gamedata.find_bundle_dir()
    if directory is None:
        print("找不到遊戲客戶端（沒裝在預設位置，或設 MAPLEEXP_GAME_DIR）", file=sys.stderr)
        return 1
    try:
        import UnityPy  # noqa: F401
    except ImportError:
        print("匯出世界地圖要 UnityPy：pip install UnityPy", file=sys.stderr)
        return 1
    worlds, strings = worldmap.load_tables(directory)
    if not worlds or not strings:
        print("讀不到客戶端的世界地圖表（沒有安裝 lz4，或客戶端格式變了）", file=sys.stderr)
        return 1
    sheets, textures = load_sheets(directory)
    if not sheets:
        print("找不到裝世界地圖的 spritesheet bundle", file=sys.stderr)
        return 1
    data = worldmap.build(worlds, strings, sheets)
    PIC_DIR.mkdir(exist_ok=True)
    for stale in PIC_DIR.iterdir():                 # 舊版的圖名字可能不一樣，全部重出
        stale.unlink()
    export_pictures(data, sheets, textures)
    icons = export_icons(directory)
    if not icons:
        print("找不到世界地圖的節點圖示", file=sys.stderr)
        return 1
    worldbook.dump(data, OUT)
    nodes = sum(len(w["nodes"]) for w in data["worlds"])
    links = sum(len(w["links"]) for w in data["worlds"])
    size = sum(p.stat().st_size for p in PIC_DIR.iterdir())
    print(f"{OUT.relative_to(ROOT)}: {len(data['worlds'])} 張世界地圖、{nodes} 個節點、{links} 個區域連結，{OUT.stat().st_size / 1024:.0f} KB\n"
          f"{PIC_DIR.relative_to(ROOT)}/: {sum(1 for _ in PIC_DIR.iterdir())} 張圖（含 {icons} 個圖示），{size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
