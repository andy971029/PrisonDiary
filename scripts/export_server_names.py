"""產生後端的合法名稱清單 ``server/names.json``。

後端（``server/app.py``）只用標準函式庫、部署時只複製檔案，讀不到網頁的圖鑑，所以把它需要的三份清單
另外寫成一個小檔，跟 ``app.py`` 一起放到主機上：

- ``jobs``：狀態列可能顯示的職業（``mapleexp.core.jobvocab.JOB_NAMES``）。辨識程式配不上清單時會照 OCR 原文存，
  那種職業不算進排行榜。
- ``maps``：網頁會上傳的地圖名稱（``web/mapvocab.json`` 的「地區 地圖」，沒有地區時只有地圖）。辨識程式一定會挑清單裡的一張，
  所以不在清單裡的就是壞資料或舊版網頁。
- ``noBoardMaps``：在上面清單裡、但經驗不算練功的地圖。目前是女神組隊任務（地圖圖鑑的 ``mark`` 是 ``GoddessTower``，
  也就是雅典娜禁地那 24 個名字），解任務拿到的經驗不算進任何排行榜。

客戶端改版、``mapvocab.json`` 或 ``mapbook.json`` 重新匯出後要重跑（``update_data.py`` 會跑），
再把 ``names.json`` 跟 ``app.py`` 一起部署。``tests/test_server.py`` 會檢查這個檔有沒有跟來源對上。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mapleexp.core.jobvocab import JOB_NAMES  # noqa: E402

OUT = ROOT / "server" / "names.json"
NO_BOARD_MARKS = ("GoddessTower",)


def full_name(street: str, name: str) -> str:
    """網頁上傳的寫法，跟 ``mapvocab`` 的 ``full_name`` 一樣。"""
    return f"{street} {name}".strip()


def build(web: Path = ROOT / "web") -> dict:
    vocab = json.loads((web / "mapvocab.json").read_text(encoding="utf-8"))
    book = json.loads((web / "mapbook.json").read_text(encoding="utf-8"))["maps"]
    maps = sorted({full_name(street, name) for _id, street, name in vocab})
    no_board = sorted({full_name(m["street"], m["name"]) for m in book if m.get("mark") in NO_BOARD_MARKS})
    return {"jobs": sorted(set(JOB_NAMES)), "maps": maps, "noBoardMaps": no_board}


def render(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=0) + "\n"


def main() -> int:
    data = build()
    OUT.write_text(render(data), encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}：職業 {len(data['jobs'])}、地圖 {len(data['maps'])}、不算排行榜的地圖 {len(data['noBoardMaps'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
