"""客戶端改版後更新全部圖鑑（``update_data.bat`` 跑這支）。

照相依順序重跑每支匯出腳本，列出各圖鑑的數量變化，跑測試，問過才升版號、提交並推送；推到 main 就會自動部署。
任何一步失敗就停下，什麼都不會提交。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
VERSION_PY = ROOT / "src" / "mapleexp" / "__init__.py"
INDEX = WEB / "index.html"
# 順序不能換：道具反查怪物圖鑑，世界圖鑑要怪物與道具，世界地圖要地圖圖鑑
EXPORTS = ["export_monsterbook", "export_itembook", "export_worldbook", "export_worldmap", "export_mapvocab"]
DATA = [
    "monsterbook.json", "itembook.json", "questbook.json", "npcbook.json", "mapbook.json", "worldmap.json", "mapvocab.json",
    "mobs", "icons", "npcs", "minimaps", "worldmap",
]


def counts(web: Path) -> dict[str, int]:
    """各圖鑑現在有幾筆；檔案不存在或壞了的算 0。"""
    def book(name: str) -> dict:
        try:
            return json.loads((web / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    items, nodes = book("itembook.json"), [n for w in book("worldmap.json").get("worlds", []) for n in w["nodes"]]
    out = {
        "怪物": len(book("monsterbook.json").get("mobs", [])),
        "道具": len(items.get("items", [])),
        "裝備": len(items.get("equips", [])),
        "任務": len(book("questbook.json").get("quests", [])),
        "NPC": len(book("npcbook.json").get("npcs", [])),
        "地圖": len(book("mapbook.json").get("maps", [])),
        "世界地圖節點": sum("via" not in n for n in nodes),
        "補的紫色節點": sum("via" in n for n in nodes),
        "地圖字庫": len(book("mapvocab.json")),
    }
    for label, folder in (("怪物圖", "mobs"), ("NPC 圖", "npcs"), ("小地圖", "minimaps"), ("世界地圖圖檔", "worldmap")):
        path = web / folder
        out[label] = sum(1 for _ in path.iterdir()) if path.is_dir() else 0
    return out


def changes(before: dict[str, int], after: dict[str, int]) -> list[str]:
    """一項一行；變少的加警示，通常是客戶端格式變了、解析漏資料，也可能真的被移除。"""
    lines = []
    for label, now in after.items():
        diff = now - before.get(label, 0)
        mark = f"+{diff}" if diff > 0 else f"{diff}  ⚠ 變少了，請確認" if diff < 0 else "不變"
        lines.append(f"  {label}：{now}（{mark}）")
    return lines


def bump(version: str) -> str:
    major, minor, patch = version.split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


def set_version(old: str, new: str) -> None:
    for path, before, after in ((VERSION_PY, f'__version__ = "{old}"', f'__version__ = "{new}"'),
                                (INDEX, f'id="app-version">v{old}<', f'id="app-version">v{new}<')):
        text = path.read_bytes().decode("utf-8")        # 不轉換換行
        if before not in text:
            raise ValueError(f"{path.name} 裡找不到版本 {old}")
        path.write_bytes(text.replace(before, after).encode("utf-8"))


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, encoding="utf-8").stdout.strip()


def publish(summary: list[str]) -> None:
    old = re.search(r'__version__ = "([^"]+)"', VERSION_PY.read_text(encoding="utf-8")).group(1)
    new = bump(old)
    set_version(old, new)
    git("add", "-A", "--", *[f"web/{name}" for name in DATA], "web/index.html", "src/mapleexp/__init__.py")
    body = "\n".join(line.strip() for line in summary if "不變" not in line)
    git("commit", "-m", f"Data: refresh the books from the updated game client (v{new})\n\n{body}")
    subprocess.run(["git", "push", "origin", "main"], cwd=ROOT, check=True)
    print(f"\n已推送 v{new}，GitHub Actions 測試通過後會自動部署。")


def main() -> int:
    if git("branch", "--show-current") != "main":
        print("目前不在 main，先切回 main 再更新。")
        return 1
    # 上次匯出沒發布留下的資料不算，重跑會覆蓋
    dirty = git("status", "--short", "--untracked-files=no", "--", ".", *[f":!web/{name}" for name in DATA])
    if dirty:
        print("有還沒提交的修改，先提交或還原，免得跟圖鑑資料混在一起：\n" + dirty)
        return 1

    before = counts(WEB)
    for index, name in enumerate(EXPORTS, 1):
        print(f"\n=== [{index}/{len(EXPORTS)}] {name} ===", flush=True)
        if subprocess.run([sys.executable, str(ROOT / "scripts" / f"{name}.py")], cwd=ROOT).returncode:
            print(f"\n{name} 失敗，沒有提交任何東西。已匯出的資料可以用 git restore web 還原（git status 看有沒有多出新檔）。")
            return 1

    summary = changes(before, counts(WEB))
    print("\n=== 數量變化 ===\n" + "\n".join(summary))
    changed = git("status", "--porcelain", "--", *[f"web/{name}" for name in DATA])
    if not changed:
        print("\n資料跟目前的版本完全一樣，不用發布。")
        return 0
    print(f"\n共 {len(changed.splitlines())} 個檔案有變動。")

    print("\n=== 測試 ===", flush=True)
    if subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "tests"], cwd=ROOT).returncode:
        print("\n測試沒過，沒有提交。修好後再跑一次，或用 git restore web 還原。")
        return 1

    print("\n可以先開 web\\serve.cmd 到 http://localhost:8765/ 看一下，再回來回答。")
    try:
        answer = input("要升版號、提交並推送嗎？輸入 y 確定，其他鍵取消：")
    except EOFError:
        answer = ""
    if answer.strip().lower() != "y":
        print("沒有發布。資料留在工作目錄，不要的話用 git restore web 還原。")
        return 0
    publish(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
