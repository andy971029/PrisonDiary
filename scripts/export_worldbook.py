"""從本機的遊戲客戶端匯出任務、NPC、地圖三份圖鑑：``web/questbook.json``、``web/npcbook.json``、
``web/mapbook.json``，加上 NPC 站姿圖 ``web/npcs/<id>.png``。

網頁「圖鑑」的任務、NPC、地圖分頁讀這些；道具與裝備的「任務獎勵」來源也從任務圖鑑反查。
格式見 ``mapleexp/worldbook.py``。NPC 圖在 ``spritesheet_*.bundle`` 的 ``Npc/<七位數 ID>.asset``，
跟怪物圖一樣用 UnityPy 讀、``mapleexp/spritesheet.py`` 解版面。客戶端改版就重跑一次。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mapleexp import gamedata, spritesheet, worldbook  # noqa: E402

OUT = {"quests": ROOT / "web" / "questbook.json", "npcs": ROOT / "web" / "npcbook.json", "maps": ROOT / "web" / "mapbook.json"}
PIC_DIR = ROOT / "web" / "npcs"
SHEET_PREFIX = "Assets/WzAssets/SpriteSheet/TW/Npc/"
MAX_SIDE = 160


def find_npc_sheets(directory: Path):
    """找裝 NPC 圖集的 bundle（小的先試），回傳 UnityPy 的 container；找不到就 None。"""
    import UnityPy

    for path in sorted(directory.glob("spritesheet_*.bundle"), key=lambda p: p.stat().st_size):
        env = UnityPy.load(str(path))
        container = dict(env.container.items())
        if any(key.startswith(SHEET_PREFIX) for key in container):
            print(f"NPC 圖集：{path.name}", file=sys.stderr)
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
    container = find_npc_sheets(directory)
    if container is None:
        print("找不到裝 NPC 圖集的 spritesheet bundle", file=sys.stderr)
        return 1
    npcs = data["npcs"]["npcs"]
    sizes = export_pictures(container, npcs)
    for npc in npcs:
        if npc["id"] in sizes:
            npc["pic"] = list(sizes[npc["id"]])
    for key, path in OUT.items():
        worldbook.dump(data[key], path)
    quests, maps = data["quests"]["quests"], data["maps"]["maps"]
    placed = sum(1 for n in npcs if n["maps"])
    pic_bytes = sum(p.stat().st_size for p in PIC_DIR.glob("*.png"))
    print(
        f"{OUT['quests'].relative_to(ROOT)}: {len(quests)} 個任務（{sum(1 for q in quests if q['give']['items'])} 個給道具）、"
        f"{len(data['quests']['cats'])} 個區域，{OUT['quests'].stat().st_size / 1024:.0f} KB\n"
        f"{OUT['npcs'].relative_to(ROOT)}: {len(npcs)} 個 NPC（{placed} 個站在已開放的地圖上），{OUT['npcs'].stat().st_size / 1024:.0f} KB\n"
        f"{PIC_DIR.relative_to(ROOT)}/: {len(sizes)} 張 NPC 圖，{pic_bytes / 1024:.0f} KB\n"
        f"{OUT['maps'].relative_to(ROOT)}: {len(maps)} 張地圖（{sum(1 for m in maps if m['town'])} 個城鎮、"
        f"{sum(1 for m in maps if m['mobs'])} 張有怪），{OUT['maps'].stat().st_size / 1024:.0f} KB"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
