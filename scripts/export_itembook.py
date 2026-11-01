"""從本機的遊戲客戶端匯出道具與裝備圖鑑 ``web/itembook.json`` 與圖示圖集 ``web/icons/*.png``。

網頁「圖鑑」的道具、裝備分頁讀這些：裝備數值、道具效果、名稱說明、掉落來源，加上每個的圖示。
先跑 ``export_monsterbook.py``（掉落來源是從 ``web/monsterbook.json`` 的圖鑑卡反查的），再跑這支。
格式見 ``mapleexp/itembook.py``。

圖示在 ``spritesheet_*.bundle``：裝備在 ``Character/<類別>/<八位數 ID>.asset`` 的 ``info/icon`` 格，道具在
``Item/<類別>/<四位數前綴>.asset`` 的 ``<八位數 ID>/info/icon`` 格。圖示小（26～32 像素）但有上萬個，一個
一個放會有上萬個檔案，所以打包成兩張圖集：每格 :data:`CELL` 像素、一列 :data:`COLS` 格，JSON 只記序號。
貼圖是 Unity 的 Texture2D，跟 ``export_monsterbook.py`` 一樣用 UnityPy 讀。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mapleexp import gamedata, itembook, spritesheet  # noqa: E402

OUT = ROOT / "web" / "itembook.json"
ICON_DIR = ROOT / "web" / "icons"
MOBS = ROOT / "web" / "monsterbook.json"
EQUIP_PREFIX = "Assets/WzAssets/SpriteSheet/TW/Character/"
ITEM_PREFIX = "Assets/WzAssets/SpriteSheet/TW/Item/"
CELL = 36
COLS = 56


def find_sheets(directory: Path) -> tuple[dict, dict] | None:
    """找裝備圖集與道具圖集所在的 bundle，回傳兩個 container（路徑 → 物件）。小的先試，兩個都找到就停。"""
    import UnityPy

    equips = items = None
    for path in sorted(directory.glob("spritesheet_*.bundle"), key=lambda p: p.stat().st_size):
        env = UnityPy.load(str(path))
        container = dict(env.container.items())
        if equips is None and any(k.startswith(EQUIP_PREFIX) for k in container):
            equips = container
            print(f"裝備圖集：{path.name}", file=sys.stderr)
        if items is None and any(k.startswith(ITEM_PREFIX) for k in container):
            items = container
            print(f"道具圖集：{path.name}", file=sys.stderr)
        if equips is not None and items is not None:
            return equips, items
    return None


class Atlas:
    """把小圖示一格一格貼進一張大圖，回傳序號。"""

    def __init__(self) -> None:
        self.crops: list = []

    def add(self, image) -> int:
        self.crops.append(image)
        return len(self.crops) - 1

    def save(self, path: Path) -> None:
        from PIL import Image

        rows = max(1, -(-len(self.crops) // COLS))
        sheet = Image.new("RGBA", (COLS * CELL, rows * CELL), (0, 0, 0, 0))
        for index, crop in enumerate(self.crops):
            if max(crop.size) > CELL:
                crop.thumbnail((CELL, CELL))
            x = (index % COLS) * CELL + (CELL - crop.width) // 2
            y = (index // COLS) * CELL + (CELL - crop.height) // 2
            sheet.paste(crop, (x, y))
        sheet.save(path, optimize=True)


def crop_icon(container: dict, asset_key: str, frame_name: str, pages: dict):
    """從某個圖集裁一格；沒那包、沒那格就 None。*pages* 是解開的貼圖快取（同一包的格共用）。"""
    asset = container.get(asset_key)
    if asset is None:
        return None
    sheet_key = asset_key[:-6]
    if sheet_key not in pages:
        try:
            pages[sheet_key] = spritesheet.parse(asset.deref().get_raw_data())
        except spritesheet.SheetError:
            pages[sheet_key] = None
    sheet = pages[sheet_key]
    frame = sheet.frame(frame_name) if sheet else None
    if frame is None or frame.width <= 0 or frame.height <= 0:
        return None
    page_key = f"{sheet_key}_{frame.page}.png"
    if page_key not in pages:
        texture = container.get(page_key)
        pages[page_key] = texture.deref().read().image.convert("RGBA") if texture is not None else None
    page = pages[page_key]
    return page.crop(frame.box(page.height)) if page is not None else None


def attach_icons(entries: list[dict], container: dict, key_of, frame_of) -> int:
    """每個條目裁圖示進圖集，記序號到 ``ic``；回傳有圖示的數量。"""
    atlas = Atlas()
    pages: dict = {}
    by_folder: dict[str, str] = {}
    for key in container:
        if key.endswith(".asset"):
            by_folder[key.rsplit("/", 1)[-1][:-6]] = key
    for entry in entries:
        asset_key = by_folder.get(key_of(entry))
        if asset_key is None:
            continue
        image = crop_icon(container, asset_key, frame_of(entry), pages)
        if image is not None:
            entry["ic"] = atlas.add(image)
        if len(pages) > 64:                   # 解開的貼圖別一直留著；裝備一件一張，會吃光記憶體
            pages.clear()
    return atlas, sum(1 for e in entries if "ic" in e)


def main() -> int:
    directory = gamedata.find_bundle_dir()
    if directory is None:
        print("找不到遊戲客戶端（沒裝在預設位置，或設 MAPLEEXP_GAME_DIR）", file=sys.stderr)
        return 1
    try:
        import UnityPy  # noqa: F401
    except ImportError:
        print("匯出圖示要 UnityPy：pip install UnityPy", file=sys.stderr)
        return 1
    if not MOBS.exists():
        print(f"先跑 scripts/export_monsterbook.py 產生 {MOBS.relative_to(ROOT)}，掉落來源要從那裡反查", file=sys.stderr)
        return 1
    mobs = json.loads(MOBS.read_text(encoding="utf-8"))["mobs"]
    data = itembook.load(directory, mobs)
    if not data:
        print("讀不到客戶端的道具資料（沒有安裝 lz4，或客戶端格式變了）", file=sys.stderr)
        return 1
    sheets = find_sheets(directory)
    if sheets is None:
        print("找不到裝圖示的 spritesheet bundle", file=sys.stderr)
        return 1
    ICON_DIR.mkdir(exist_ok=True)
    equip_atlas, n_equip = attach_icons(data["equips"], sheets[0], lambda e: f"{e['id']:08d}", lambda e: "info/icon")
    item_atlas, n_item = attach_icons(data["items"], sheets[1], lambda e: f"{e['id']:08d}"[:4],
                                      lambda e: f"{e['id']:08d}/info/icon")
    equip_atlas.save(ICON_DIR / "equips.png")
    item_atlas.save(ICON_DIR / "items.png")
    data = {"cell": CELL, "cols": COLS, **data}
    itembook.dump(data, OUT)
    sizes = {p.name: p.stat().st_size // 1024 for p in ICON_DIR.glob("*.png")}
    print(
        f"{OUT.relative_to(ROOT)}: {len(data['equips'])} 件裝備（{n_equip} 有圖示）、"
        f"{len(data['items'])} 個道具（{n_item} 有圖示），{OUT.stat().st_size / 1024:.0f} KB\n"
        f"{ICON_DIR.relative_to(ROOT)}/: {sizes}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
