"""把紙娃娃資料推到另一個 repo（``PrisonDiary-avatar``），由它自己的 GitHub Pages 提供。

主站加上這 100 多 MB 之後，GitHub Pages 部署常卡在同步檔案，所以紙娃娃另外放：網站上的頁面從
``https://<帳號>.github.io/PrisonDiary-avatar/`` 讀（同一個網域，不用跨來源），主站的部署就不帶這些。
資料本身還是在這個 repo 的 ``web/avatar.json`` 與 ``web/avatar/``，這支只是發布。

那個 repo 只是發布用的：每次都是一個沒有歷史的新 commit 強制推上去，repo 不會越積越大。
上一版的 ``.bin`` 包留著一版：Pages 會把舊的 ``avatar.json`` 快取十分鐘，那段時間它指到的包還要在。
``update_data.py`` 發布時，紙娃娃有變就會叫這支；也可以自己跑。
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
REPO = "PrisonDiary-avatar"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, encoding="utf-8").stdout.strip()


def force_remove(func, path, _exc) -> None:
    """git 的物件檔在 Windows 是唯讀的，rmtree 刪不掉：拿掉唯讀再刪。"""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remote_url() -> str:
    """跟主站同一個帳號底下：從 origin 的網址換掉 repo 名字。"""
    origin = git(ROOT, "remote", "get-url", "origin")
    head, _, _ = origin.rstrip("/").removesuffix(".git").rpartition("/")
    return f"{head}/{REPO}.git"


def main() -> int:
    if not (WEB / "avatar.json").is_file() or not (WEB / "avatar").is_dir():
        print("web/avatar.json 或 web/avatar/ 不在，先跑 scripts/export_avatar.py", file=sys.stderr)
        return 1
    url = remote_url()
    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp) / "site"
        keep: set[str] = set()
        if subprocess.run(["git", "clone", "--depth", "1", "--quiet", url, str(site)], capture_output=True).returncode == 0:
            keep = {p.name for p in (site / "avatar").glob("*.bin")} if (site / "avatar").is_dir() else set()
            shutil.rmtree(site / ".git", onexc=force_remove)
        else:
            site.mkdir()
        # 上一版的包留著，其他舊東西清掉，再放這一版
        for path in sorted(site.rglob("*"), reverse=True):
            if path.is_file() and not (path.parent.name == "avatar" and path.name in keep):
                path.unlink()
        (site / "avatar").mkdir(exist_ok=True)
        for path in (WEB / "avatar").iterdir():
            shutil.copy2(path, site / "avatar" / path.name)
        shutil.copy2(WEB / "avatar.json", site / "avatar.json")
        (site / ".nojekyll").write_text("", encoding="utf-8")           # 原封不動地提供，不要 Jekyll 處理
        (site / "README.md").write_text(
            "服刑日誌網頁版「紙娃娃」分頁的資料，由主 repo 的 scripts/publish_avatar.py 產生並強制推送，不要直接改這裡。\n",
            encoding="utf-8")
        old = len(keep - {p.name for p in (WEB / "avatar").glob("*.bin")})
        git(site, "init", "--quiet", "--initial-branch", "main")
        git(site, "add", "-A")
        source = git(ROOT, "rev-parse", "--short", "HEAD")
        git(site, "-c", "user.name=" + git(ROOT, "config", "user.name"), "-c", "user.email=" + git(ROOT, "config", "user.email"),
            "commit", "--quiet", "-m", f"紙娃娃資料（主 repo {source}）")
        subprocess.run(["git", "push", "--force", "--quiet", url, "main"], cwd=site, check=True)
    print(f"已推送到 {url}（另外留著上一版的 {old} 個包），GitHub Pages 幾分鐘內會更新。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
