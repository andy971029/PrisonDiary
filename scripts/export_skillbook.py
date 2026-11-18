"""從本機的遊戲客戶端匯出職業與技能 ``web/skillbook.json``，以及技能圖示的圖集 ``web/icons/skills.png``。

網頁「職業」分頁讀這些：每個職業的轉職分支、技能、每一級的效果。格式見 ``mapleexp/skillbook.py``。客戶端改版就重跑一次。

文字與數值不需要別的套件；``lz4`` 裝不到 wheel 的 Python（3.15 在 Windows 上就是）會改用純 Python 的解壓，慢一點但跑得完。

圖示要 UnityPy：在 ``spritesheet_*.bundle`` 的 ``Skill/<職業代碼>.asset``，格子叫 ``skill/<七位數技能 ID>/icon``。
跟道具圖示一樣打包成圖集（每格 :data:`CELL` 像素、一列 :data:`COLS` 格），技能只記序號 ``ic``。沒有 UnityPy 的環境
就不動圖集，把上一次匯出的序號照技能 ID 抄過來，文字照樣更新。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from mapleexp import gamedata, skillbook  # noqa: E402

OUT = ROOT / "web" / "skillbook.json"
ICONS = ROOT / "web" / "icons" / "skills.png"
SHEET_PREFIX = "Assets/WzAssets/SpriteSheet/TW/Skill/"
CELL = 36
COLS = 56


def skills_of(data: dict) -> list[dict]:
    return [skill for job in data["jobs"] for skill in job["skills"]]


def attach_icons(data: dict, directory: Path) -> int | None:
    """每個技能裁圖示進圖集、記序號到 ``ic``，存成 :data:`ICONS`；回傳有圖示的數量。找不到圖集回 None。"""
    import UnityPy

    from export_itembook import Atlas, crop_icon

    container = None
    for path in sorted(directory.glob("spritesheet_*.bundle"), key=lambda p: p.stat().st_size):
        found = dict(UnityPy.load(str(path)).container.items())
        if any(key.startswith(SHEET_PREFIX) for key in found):
            container = found
            print(f"技能圖集：{path.name}", file=sys.stderr)
            break
    if container is None:
        return None
    atlas = Atlas()
    for job in data["jobs"]:
        pages: dict = {}                      # 解開的貼圖只留這個職業的，換職業就丟
        key = f"{SHEET_PREFIX}{job['id']:03d}.asset"
        for skill in job["skills"]:
            image = crop_icon(container, key, f"skill/{skill['id']:07d}/icon", pages)
            if image is not None:
                skill["ic"] = atlas.add(image)
    ICONS.parent.mkdir(exist_ok=True)
    atlas.save(ICONS)
    return sum(1 for skill in skills_of(data) if "ic" in skill)


def carry_icons(data: dict) -> int:
    """沒辦法重匯圖示時：圖集留著，上一次的序號照技能 ID 抄過來。回傳抄到幾個。"""
    try:
        old = {skill["id"]: skill["ic"] for skill in skills_of(json.loads(OUT.read_text(encoding="utf-8"))) if "ic" in skill}
    except (OSError, ValueError, KeyError):
        old = {}
    if not ICONS.is_file():
        return 0
    for skill in skills_of(data):
        if skill["id"] in old:
            skill["ic"] = old[skill["id"]]
    return sum(1 for skill in skills_of(data) if "ic" in skill)


def main() -> int:
    if gamedata._lz4 is None:
        from mapleexp import lz4py

        gamedata._lz4 = lz4py
        print("沒有 lz4，改用純 Python 解壓（比較慢）", file=sys.stderr)
    directory = gamedata.find_bundle_dir()
    if directory is None:
        print("找不到遊戲客戶端。請先開著遊戲，或設定 MAPLEEXP_GAME_DIR。", file=sys.stderr)
        return 1
    data = skillbook.load(directory)
    if data is None:
        print("客戶端裡找不到技能資料或技能字串表（格式可能改了）。", file=sys.stderr)
        return 1
    try:
        import UnityPy  # noqa: F401
    except ImportError:
        icons = carry_icons(data)
        print(f"沒有 UnityPy，圖示沒有重匯：沿用上一次的圖集，{icons} 個技能有圖示", file=sys.stderr)
    else:
        icons = attach_icons(data, directory)
        if icons is None:
            print("找不到裝技能圖示的 spritesheet bundle", file=sys.stderr)
            return 1
    skillbook.dump({"cell": CELL, "cols": COLS, **data}, OUT)
    skills = skills_of(data)
    print(f"{len(data['jobs'])} 個職業、{len(skills)} 個技能（其中 {sum(1 for s in skills if s.get('hidden'))} 個是技能視窗不列的），"
          f"{icons} 個有圖示 -> {OUT}（{OUT.stat().st_size / 1024:.0f} KB）"
          + (f"、{ICONS}（{ICONS.stat().st_size / 1024:.0f} KB）" if ICONS.is_file() else ""))
    for mark, names in sorted(skillbook.unknown_placeholders(data).items()):
        print(f"  算不出的代號 #{mark}：{'、'.join(dict.fromkeys(names))}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
