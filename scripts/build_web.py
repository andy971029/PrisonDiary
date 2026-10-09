"""把 ``src/mapleexp`` 打包成瀏覽器要載入的 ``web/mapleexp.zip``。

Pyodide 用 ``unpackArchive`` 把 zip 解到它的虛擬檔案系統，之後 ``import mapleexp``
就能用。``win32/`` 裡放的已經是瀏覽器用的替身，所以整包照搬，只排掉快取檔。
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "mapleexp"
OUT = ROOT / "web" / "mapleexp.zip"


def main() -> int:
    count = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(SRC.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            zf.write(path, f"mapleexp/{path.relative_to(SRC).as_posix()}")
            count += 1
    print(f"{OUT.relative_to(ROOT)}: {count} files, {OUT.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
