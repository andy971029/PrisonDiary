"""從本機的遊戲客戶端匯出任務、NPC、地圖三份圖鑑：``web/questbook.json``、``web/npcbook.json``、
``web/mapbook.json``，加上 NPC 站姿圖 ``web/npcs/<id>.png`` 與小地圖 ``web/minimaps/<id>.png``。

網頁「圖鑑」的任務、NPC、地圖分頁讀這些；道具與裝備的「任務獎勵」來源也從任務圖鑑反查。
格式見 ``mapleexp/worldbook.py``。NPC 圖在 ``spritesheet_*.bundle`` 的 ``Npc/<七位數 ID>.asset``，
跟怪物圖一樣用 UnityPy 讀、``mapleexp/spritesheet.py`` 解版面；小地圖是 ``Map/Map/Map<n>/<地圖 ID>.asset``
的 ``miniMap/canvas`` 那一格。客戶端改版就重跑一次。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mapleexp import gamedata, spritesheet, worldbook  # noqa: E402

OUT = {"quests": ROOT / "web" / "questbook.json", "npcs": ROOT / "web" / "npcbook.json", "maps": ROOT / "web" / "mapbook.json"}
PIC_DIR = ROOT / "web" / "npcs"
MINI_DIR = ROOT / "web" / "minimaps"
SHEET_PREFIX = "Assets/WzAssets/SpriteSheet/TW/Npc/"
MAP_SHEET_PREFIX = "Assets/WzAssets/SpriteSheet/TW/Map/Map/"
MAP_SHEET = re.compile(r"/(\d+)\.asset$")
MINI_FRAME = "miniMap/canvas"
MAX_SIDE = 160


def find_sheets(directory: Path, prefix: str):
    """找裝 *prefix* 這類圖集的 bundle（小的先試），回傳 UnityPy 的 container；找不到就 None。"""
    import UnityPy

    for path in sorted(directory.glob("spritesheet_*.bundle"), key=lambda p: p.stat().st_size):
        env = UnityPy.load(str(path))
        container = dict(env.container.items())
        if any(key.startswith(prefix) for key in container):
            print(f"{prefix} 圖集：{path.name}", file=sys.stderr)
            return container
    return None


def export_pictures(container, npcs: list[dict]) -> dict[int, tuple[int, int]]:
    """每個有圖集的 NPC 裁一張站姿圖到 ``web/npcs/``，回傳 ``{id: (寬, 高)}``。舊的、現在沒有的圖一併刪掉。"""
    from PIL import Image

    PIC_DIR.mkdir(exist_ok=True)
    sizes: dict[int, tuple[int, int]] = {}
    for npc in npcs:
        key = f"{SHEET_PREFIX}{npc['id']:07d}"
        asset = container.get(key + ".asset")
        if asset is None:
            continue
        try:
            sheet = spritesheet.parse(asset.deref().get_raw_data())
        except spritesheet.SheetError as exc:
            print(f"  {npc['id']} 圖集解不開：{exc}", file=sys.stderr)
            continue
        frame = spritesheet.pick_portrait(sheet)
        texture = container.get(f"{key}_{frame.page}.png") if frame else None
        if texture is None:
            continue
        page = texture.deref().read().image.convert("RGBA")
        image = page.crop(frame.box(page.height))
        if max(image.size) > MAX_SIDE:
            image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        image.save(PIC_DIR / f"{npc['id']}.png", optimize=True)
        sizes[npc["id"]] = image.size
    for stale in PIC_DIR.glob("*.png"):
        if not (stale.stem.isdigit() and int(stale.stem) in sizes):
            stale.unlink()
    return sizes


def export_minimaps(container, maps: list[dict]) -> int:
    """每張有小地圖的地圖裁一張到 ``web/minimaps/``，尺寸以實際的圖為準；沒圖的地圖拿掉 ``mini``。回傳張數。"""
    MINI_DIR.mkdir(exist_ok=True)
    assets = {int(hit.group(1)): key for key in container if key.startswith(MAP_SHEET_PREFIX) and (hit := MAP_SHEET.search(key))}
    saved: set[int] = set()
    for entry in maps:
        mini = entry.get("mini")
        key = assets.get(entry["id"])
        image = None
        if mini and key:
            try:
                frame = spritesheet.parse(container[key].deref().get_raw_data()).frame(MINI_FRAME)
            except spritesheet.SheetError as exc:
                print(f"  {entry['id']} 圖集解不開：{exc}", file=sys.stderr)
                frame = None
            texture = container.get(f"{key.removesuffix('.asset')}_{frame.page}.png") if frame else None
            if texture is not None:
                page = texture.deref().read().image.convert("RGBA")
                image = page.crop(frame.box(page.height))
        if image is None:
            entry.pop("mini", None)
            continue
        image.save(MINI_DIR / f"{entry['id']}.png", optimize=True)
        mini["w"], mini["h"] = image.size
        saved.add(entry["id"])
    for stale in MINI_DIR.glob("*.png"):
        if not (stale.stem.isdigit() and int(stale.stem) in saved):
            stale.unlink()
    return len(saved)


def main() -> int:
    directory = gamedata.find_bundle_dir()
    if directory is None:
        print("找不到遊戲客戶端（沒裝在預設位置，或設 MAPLEEXP_GAME_DIR）", file=sys.stderr)
        return 1
    try:
        import UnityPy  # noqa: F401
    except ImportError:
        print("匯出 NPC 圖要 UnityPy：pip install UnityPy", file=sys.stderr)
        return 1
    data = worldbook.load(directory)
    if not data:
        print("讀不到客戶端的任務或 NPC 資料（沒有安裝 lz4，或客戶端格式變了）", file=sys.stderr)
        return 1
    container = find_sheets(directory, SHEET_PREFIX)
    map_container = find_sheets(directory, MAP_SHEET_PREFIX)
    if container is None or map_container is None:
        print("找不到裝 NPC 或地圖圖集的 spritesheet bundle", file=sys.stderr)
        return 1
    npcs = data["npcs"]["npcs"]
    sizes = export_pictures(container, npcs)
    for npc in npcs:
        if npc["id"] in sizes:
            npc["pic"] = list(sizes[npc["id"]])
    minimaps = export_minimaps(map_container, data["maps"]["maps"])
    for key, path in OUT.items():
        worldbook.dump(data[key], path)
    quests, maps = data["quests"]["quests"], data["maps"]["maps"]
    placed = sum(1 for n in npcs if n["maps"])
    pic_bytes = sum(p.stat().st_size for p in PIC_DIR.glob("*.png"))
    mini_bytes = sum(p.stat().st_size for p in MINI_DIR.glob("*.png"))
    print(
        f"{OUT['quests'].relative_to(ROOT)}: {len(quests)} 個任務（{sum(1 for q in quests if q['give']['items'])} 個給道具）、"
        f"{len(data['quests']['cats'])} 個區域，{OUT['quests'].stat().st_size / 1024:.0f} KB\n"
        f"{OUT['npcs'].relative_to(ROOT)}: {len(npcs)} 個 NPC（{placed} 個站在已開放的地圖上），{OUT['npcs'].stat().st_size / 1024:.0f} KB\n"
        f"{PIC_DIR.relative_to(ROOT)}/: {len(sizes)} 張 NPC 圖，{pic_bytes / 1024:.0f} KB\n"
        f"{OUT['maps'].relative_to(ROOT)}: {len(maps)} 張地圖（{sum(1 for m in maps if m['town'])} 個城鎮、"
        f"{sum(1 for m in maps if m['mobs'])} 張有怪），{OUT['maps'].stat().st_size / 1024:.0f} KB\n"
        f"{MINI_DIR.relative_to(ROOT)}/: {minimaps} 張小地圖，{mini_bytes / 1024:.0f} KB"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
