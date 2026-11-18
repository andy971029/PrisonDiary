"""從本機的遊戲客戶端匯出職業與技能 ``web/skillbook.json``。

網頁「職業」分頁讀這份：每個職業的轉職分支、技能、每一級的效果。格式見 ``mapleexp/skillbook.py``。
只讀文字與數值，不需要 UnityPy（技能圖示在 ``spritesheet_*.bundle``，這支不匯）。客戶端改版就重跑一次。

``lz4`` 裝不到 wheel 的 Python（3.15 在 Windows 上就是）會改用純 Python 的解壓，慢一點但跑得完。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mapleexp import gamedata, skillbook  # noqa: E402

OUT = ROOT / "web" / "skillbook.json"


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
    skillbook.dump(data, OUT)
    skills = [s for job in data["jobs"] for s in job["skills"]]
    print(f"{len(data['jobs'])} 個職業、{len(skills)} 個技能（其中 {sum(1 for s in skills if s.get('hidden'))} 個是技能視窗不列的）"
          f" -> {OUT}（{OUT.stat().st_size / 1024:.0f} KB）")
    for mark, names in sorted(skillbook.unknown_placeholders(data).items()):
        print(f"  算不出的代號 #{mark}：{'、'.join(dict.fromkeys(names))}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
