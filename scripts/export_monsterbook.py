"""從本機的遊戲客戶端匯出怪物圖鑑快照 ``web/monsterbook.json`` 與怪物圖 ``web/mobs/<id>.png``。

網頁的「怪物圖鑑」分頁讀這些：怪物數值、圖鑑卡上的掉落物與出現地圖、各種名稱，加上每隻怪的
站姿圖。瀏覽器讀不到客戶端的 bundle，所以跟 ``export_mapvocab.py`` 一樣先在本機匯出。
客戶端改版（加怪、改數值、換圖）就重跑一次。JSON 格式見 ``mapleexp/monsterbook.py``。

怪物圖在 ``spritesheet_*.bundle`` 裡：每隻怪一包自訂的圖集（``mapleexp/spritesheet.py`` 解）配
幾張 RGBA32 貼圖。貼圖是 Unity 的 Texture2D，要解它的 SerializedFile 版面，這裡直接用 UnityPy
（只有這支腳本需要，``pip install UnityPy``，Pillow 會跟著裝）。挑站姿第一格裁下來，太大的
Boss 縮到 :data:`MAX_SIDE` 像素以內，網頁上本來就只顯示小圖。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mapleexp import gamedata, monsterbook, spritesheet  # noqa: E402

OUT = ROOT / "web" / "monsterbook.json"
PIC_DIR = ROOT / "web" / "mobs"
SHEET_PREFIX = "Assets/WzAssets/SpriteSheet/TW/Mob/"
MAX_SIDE = 256


def find_mob_sheets(directory: Path):
    """找裝怪物圖集的 bundle，回傳 UnityPy 的 container（路徑 → 物件）；找不到就 None。

    哪一包沒寫在檔名裡，只能一包一包打開看。大的先試：怪物那包是前幾大的。
    """
    import UnityPy

    bundles = sorted(directory.glob("spritesheet_*.bundle"), key=lambda p: p.stat().st_size, reverse=True)
    for path in bundles:
        env = UnityPy.load(str(path))
        container = dict(env.container.items())
        if any(key.startswith(SHEET_PREFIX) for key in container):
            print(f"怪物圖集：{path.name}", file=sys.stderr)
            return container
    return None


def export_pictures(container, mobs: list[dict]) -> dict[int, tuple[int, int]]:
    """每隻怪裁一張站姿圖到 ``web/mobs/``，回傳 ``{id: (寬, 高)}``。舊的、現在沒有的圖一併刪掉。"""
    from PIL import Image

    PIC_DIR.mkdir(exist_ok=True)
    pages: dict[str, Image.Image] = {}
    sizes: dict[int, tuple[int, int]] = {}
    for mob in mobs:
        key = f"{SHEET_PREFIX}{mob['id']:07d}"
        asset = container.get(key + ".asset")
        if asset is None:
            continue
        try:
            sheet = spritesheet.parse(asset.deref().get_raw_data())
        except spritesheet.SheetError as exc:
            print(f"  {mob['id']} 圖集解不開：{exc}", file=sys.stderr)
            continue
        frame = spritesheet.pick_portrait(sheet)
        texture = container.get(f"{key}_{frame.page}.png") if frame else None
        if texture is None:
            continue
        page_key = f"{key}_{frame.page}"
        if page_key not in pages:
            pages.clear()                          # 一隻怪的頁用完就丟，不然 2048² 的 Boss 貼圖會吃光記憶體
            pages[page_key] = texture.deref().read().image.convert("RGBA")
        page = pages[page_key]
        image = page.crop(frame.box(page.height))
        if max(image.size) > MAX_SIDE:
            image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        image.save(PIC_DIR / f"{mob['id']}.png", optimize=True)
        sizes[mob["id"]] = image.size
    for stale in PIC_DIR.glob("*.png"):
        if not (stale.stem.isdigit() and int(stale.stem) in sizes):
            stale.unlink()
    return sizes


def main() -> int:
    directory = gamedata.find_bundle_dir()
    if directory is None:
        print("找不到遊戲客戶端（沒裝在預設位置，或設 MAPLEEXP_GAME_DIR）", file=sys.stderr)
        return 1
    try:
        import UnityPy  # noqa: F401
    except ImportError:
        print("匯出怪物圖要 UnityPy：pip install UnityPy", file=sys.stderr)
        return 1
    data = monsterbook.load(directory)
    if not data:
        print("讀不到客戶端的怪物資料（沒有安裝 lz4，或客戶端格式變了）", file=sys.stderr)
        return 1
    container = find_mob_sheets(directory)
    if container is None:
        print("找不到裝怪物圖集的 spritesheet bundle", file=sys.stderr)
        return 1
    sizes = export_pictures(container, data["mobs"])
    for mob in data["mobs"]:
        if mob["id"] in sizes:
            mob["pic"] = list(sizes[mob["id"]])
    monsterbook.dump(data, OUT)
    with_card = sum(1 for m in data["mobs"] if m["drops"] or m["maps"])
    pic_bytes = sum(p.stat().st_size for p in PIC_DIR.glob("*.png"))
    print(
        f"{OUT.relative_to(ROOT)}: {len(data['mobs'])} 隻怪物（{with_card} 隻有圖鑑卡）、"
        f"{len(data['items'])} 種掉落物、{len(data['maps'])} 張地圖，{OUT.stat().st_size / 1024:.0f} KB\n"
        f"{PIC_DIR.relative_to(ROOT)}/: {len(sizes)} 張怪物圖，{pic_bytes / 1024:.0f} KB"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
