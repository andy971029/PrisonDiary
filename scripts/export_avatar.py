"""從本機的遊戲客戶端匯出時裝紙娃娃：``web/avatar.json`` 與 ``web/avatar/``。

網頁「紙娃娃」分頁讀這些。格式見 ``mapleexp/avatar.py``。部位資料跟裝備同一包 WZJS，圖在
``spritesheet_*.bundle`` 的 ``Character/``，用 UnityPy 讀；有些時裝的圖在 ``Effect/ItemEff``，另外找那包。每件是一段 JSON 加一張 WebP 圖集，依 :func:`mapleexp.avatar.pack_key` 併成幾十個 ``.bin`` 包，
網頁照 ``avatar.json`` 記的位置用 Range 只抓那一段。
客戶端改版就重跑一次（``update_data.bat`` 會跑）。
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from export_itembook import EQUIP_PREFIX, Atlas, crop_icon, find_sheets  # noqa: E402
from mapleexp import avatar, gamedata, itembook, wzjs  # noqa: E402

OUT = ROOT / "web" / "avatar.json"
DIR = ROOT / "web" / "avatar"
PAD = 1
ATLAS_WIDTH = 256
EFFECT_SHEET = "Assets/WzAssets/SpriteSheet/TW/Effect/ItemEff.asset"
EFFECT_MARKER = struct.pack("<i", 7) + b"ItemEff\0"


class Effects:
    """``Effect/ItemEff``：資料樹、圖集所在的 container、解開的圖集快取（每件都要用，留著）。"""

    def __init__(self, tree: dict, container: dict) -> None:
        self.tree, self.container, self.pages = tree, container, {}


def find_effects(directory: Path) -> Effects | None:
    import UnityPy

    found = gamedata.find_bundle_containing(directory, EFFECT_MARKER, limit=itembook.EQUIP_BUNDLE_LIMIT)
    tree = next((t for n, t in wzjs.iter_assets(found[1], canvas=True) if n == "ItemEff"), None) if found else None
    if tree is None:
        return None
    for path in sorted(directory.glob("spritesheet_*.bundle"), key=lambda p: p.stat().st_size):
        container = dict(UnityPy.load(str(path)).container.items())
        if EFFECT_SHEET in container:
            print(f"特效圖集：{path.name}", file=sys.stderr)
            return Effects(tree, container)
    return None


def pack(crops: list) -> tuple[list[tuple[int, int]], object]:
    """由高到矮排成一列列，回傳每張的左上角與整張圖集。"""
    from PIL import Image

    order = sorted(range(len(crops)), key=lambda i: -crops[i].height)
    area = sum((c.width + PAD) * (c.height + PAD) for c in crops)
    width = max(ATLAS_WIDTH, math.ceil(math.sqrt(area)), *(c.width + PAD for c in crops))     # 動作多的不要排得太高，WebP 限 16383 像素
    spots = [(0, 0)] * len(crops)
    x = y = row = 0
    for index in order:
        crop = crops[index]
        if x + crop.width > width:
            x, y, row = 0, y + row + PAD, 0
        spots[index] = (x, y)
        x += crop.width + PAD
        row = max(row, crop.height)
    sheet = Image.new("RGBA", (width, y + row))
    for index, crop in enumerate(crops):
        sheet.paste(crop, spots[index])
    return spots, sheet


def build_item(item_id: int, folder: str, tree: dict, container: dict, extra: dict | None = None, effects: Effects | None = None) -> tuple[bytes, bytes] | None:
    """一件的 ``(JSON, WebP 圖集)``；一格圖都裁不到就 None。"""
    parts = avatar.Parts()
    if folder == "Weapon":
        groups = {g: avatar.collect_actions(tree, [g], parts) for g in avatar.weapon_groups(tree)}
        data: dict = {"w": {g: a for g, a in groups.items() if a}}
    else:
        data = {"a": avatar.collect_actions(tree, [], parts) or avatar.collect_default(tree, parts)}
    if effects and (effect := avatar.collect_effect(effects.tree, str(item_id), parts, EFFECT_SHEET)):
        data["e"] = effect
    if extra:
        data.update(extra)
    asset_key = f"{EQUIP_PREFIX}{folder + '/' if folder else ''}{item_id:08d}.asset"
    pages: dict = {}
    crops, rows = [], []
    for row in parts.rows:
        if row.get("sheet") and effects:
            crops.append(crop_icon(effects.container, row["sheet"], row["path"], effects.pages))
        else:
            crops.append(crop_icon(container, asset_key, row["path"], pages))
        rows.append(row)
    usable = [c for c in crops if c is not None]
    if not usable:
        return None
    spots, sheet = pack(usable)
    table = []
    at = 0
    for row, crop in zip(rows, crops):
        if crop is None:
            table.append(None)
            continue
        table.append([*spots[at], crop.width, crop.height, *row["origin"], row["z"], row["map"]])
        at += 1
    buffer = io.BytesIO()
    sheet.save(buffer, "WEBP", lossless=True, quality=100, method=4)
    data["p"] = table
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), buffer.getvalue()


def write_packs(packs: dict[str, list[tuple[int, bytes, bytes]]]) -> list:
    """每包依 ID 排好、一件一件接起來寫檔。檔名帶內容雜湊，改版後瀏覽器不會拿舊包配新位置。
    回傳 ``[[檔名, [[ID, JSON 長度, 圖長度], …]], …]``，位置照順序累加。"""
    index = []
    for key in sorted(packs):
        entries = sorted(packs[key], key=lambda e: e[0])
        blob = b"".join(j + w for _, j, w in entries)
        name = f"{key}.{hashlib.sha1(blob).hexdigest()[:10]}.bin"
        (DIR / name).write_bytes(blob)
        index.append([name, [[i, len(j), len(w)] for i, j, w in entries]])
    return index


def hair_thumb(item_id: int, tree: dict, container: dict):
    """髮型縮圖：正面的頭髮兩層照 origin 疊起來。"""
    from PIL import Image

    default = tree.get("default") or {}
    layers = []
    pages: dict = {}
    for part in ("hair", "hairOverHead"):
        node = default.get(part)
        if isinstance(node, dict) and "origin" in node:
            image = crop_icon(container, f"{EQUIP_PREFIX}Hair/{item_id:08d}.asset", f"default/{part}", pages)
            if image is not None:
                layers.append((image, round(node["origin"].x), round(node["origin"].y)))
    if not layers:
        return None
    left = max(o[1] for o in layers)
    top = max(o[2] for o in layers)
    width = max(o[0].width - o[1] for o in layers) + left
    height = max(o[0].height - o[2] for o in layers) + top
    out = Image.new("RGBA", (width, height))
    for image, ox, oy in layers:
        out.alpha_composite(image, (left - ox, top - oy))
    return out


def main() -> int:
    directory = gamedata.find_bundle_dir()
    if directory is None:
        print("找不到遊戲客戶端（沒裝在預設位置，或設 MAPLEEXP_GAME_DIR）", file=sys.stderr)
        return 1
    try:
        import UnityPy  # noqa: F401
    except ImportError:
        print("匯出紙娃娃要 UnityPy：pip install UnityPy", file=sys.stderr)
        return 1
    hit = gamedata.read_table(directory, itembook.ITEM_TABLE)
    bundle = gamedata.find_bundle_containing(directory, itembook.EQUIP_MARKER, limit=itembook.EQUIP_BUNDLE_LIMIT)
    sheets = find_sheets(directory)
    if not hit or not bundle or sheets is None:
        print("讀不到客戶端的角色資料（沒有安裝 lz4，或客戶端格式變了）", file=sys.stderr)
        return 1
    names = json.loads(hit[1].decode("utf-8-sig"))
    name_of = lambda i: ((names.get(str(i)) or {}).get("name") or "").strip() or None  # noqa: E731
    assets = {n: t for n, t in wzjs.iter_assets(bundle[1], canvas=True) if len(n) == 8 and n.isdigit()}
    container = sheets[0]
    effects = find_effects(directory)
    if effects is None:
        print("找不到 Effect/ItemEff，圖放在特效裡的時裝（不少披風）會是空的", file=sys.stderr)
    DIR.mkdir(exist_ok=True)
    for stale in DIR.iterdir():                     # 下架的時裝不要留著
        stale.unlink()

    unknown: dict[str, int] = {}
    def note(tree: object) -> None:
        if isinstance(tree, dict):
            z = tree.get("z")
            # 數字是特效的層，本來就放最上面，不用提醒
            if z is not None and avatar.clean_z(z) == "characterEnd" and z != "characterEnd" and not str(z).lstrip("-").isdigit():
                unknown[str(z)] = unknown.get(str(z), 0) + 1
            for value in tree.values():
                note(value)

    total = written = 0
    packs: dict[str, list[tuple[int, bytes, bytes]]] = {}
    def emit(item_id: int, folder: str, tree: dict, extra: dict | None = None, effects: Effects | None = None) -> bool:
        nonlocal total, written
        note(tree)
        built = build_item(item_id, folder, tree, container, extra, effects)
        if built is None:
            return False
        packs.setdefault(avatar.pack_key(item_id), []).append((item_id, *built))
        total += len(built[0]) + len(built[1])
        written += 1
        if written % 500 == 0:
            print(f"  {written} 件，{total / 1048576:.0f} MB", file=sys.stderr, flush=True)
        return True

    skins = []
    for name, tree in sorted(assets.items()):
        if name.startswith("0000") and name[4] == "2":
            skin = int(name) - 2000
            head = assets.get(f"{12000 + skin:08d}")
            label = name_of(12000 + skin)
            if head is None or not label:
                continue
            if emit(int(name), "", tree, {"d": avatar.delays(tree)}) and emit(12000 + skin, "", head):
                skins.append({"id": skin, "n": label})

    def of_kind(prefixes: tuple[str, ...]) -> list[int]:
        return [int(n) for n in assets if n.startswith(prefixes) and name_of(int(n))]

    hair_ids = [i for i in of_kind(("0003", "0004")) if emit(i, "Hair", assets[f"{i:08d}"])]
    face_ids = [i for i in of_kind(("0002",)) if "default" in assets[f"{i:08d}"] and emit(i, "Face", assets[f"{i:08d}"])]
    hair = avatar.hair_styles(hair_ids, name_of)
    face = avatar.face_styles(face_ids, name_of)

    thumbs = {"hair": Atlas(), "face": Atlas()}
    pages: dict = {}
    for style in hair:
        image = hair_thumb(next(iter(style["c"].values())), assets[f"{next(iter(style['c'].values())):08d}"], container)
        if image is not None:
            style["ic"] = thumbs["hair"].add(image)
    for style in face:
        item = next(iter(style["c"].values()))
        image = crop_icon(container, f"{EQUIP_PREFIX}Face/{item:08d}.asset", "default/face", pages)
        if image is not None:
            style["ic"] = thumbs["face"].add(image)
        if len(pages) > 64:
            pages.clear()

    icons = Atlas()
    pages = {}
    items: dict[str, list] = {key: [] for key, _, _ in avatar.CATEGORIES.values()}
    for name, tree in sorted(assets.items()):
        category = avatar.CATEGORIES.get(name[1:4]) if name.startswith("01") else None
        info = tree.get("info") if isinstance(tree.get("info"), dict) else {}
        label = name_of(int(name))
        if category is None or not info.get("cash") or not label:
            continue
        key, folder, _ = category
        if not emit(int(name), folder, tree, effects=effects):
            continue
        entry: dict = {"id": int(name), "n": label}
        if isinstance(info.get("vslot"), str):
            entry["v"] = info["vslot"]
        if folder == "Weapon":
            entry["g"] = [int(g) for g in avatar.weapon_groups(tree)]
        image = crop_icon(container, f"{EQUIP_PREFIX}{folder}/{name}.asset", "info/icon", pages)
        if image is not None:
            entry["ic"] = icons.add(image)
        items[key].append(entry)
        if len(pages) > 64:
            pages.clear()

    icons.save(DIR / "icons.png")
    thumbs["hair"].save(DIR / "hair.png")
    thumbs["face"].save(DIR / "face.png")
    index = write_packs(packs)
    OUT.write_text(json.dumps({
        "order": avatar.ORDER, "skins": skins, "hair": hair, "face": face, "items": items, "packs": index,
        "cats": [[key, label] for key, _, label in avatar.CATEGORIES.values()],
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    count = sum(len(v) for v in items.values())
    print(f"{OUT.relative_to(ROOT)}: {len(skins)} 種膚色、{len(hair)} 款髮型、{len(face)} 款臉型、{count} 件時裝，{OUT.stat().st_size / 1024:.0f} KB\n"
          f"{DIR.relative_to(ROOT)}/: {written} 件併成 {len(index)} 包，{total / 1048576:.0f} MB")
    if unknown:
        print(f"這些圖層不在 avatar.ORDER 裡，先畫在最上層，請補進去：{unknown}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
