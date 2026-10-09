# 服刑日誌（網頁版）

楓之谷：經典版的經驗值計算器，整個在瀏覽器裡跑。用瀏覽器的「分享視窗」看遊戲畫面，
讀狀態列的經驗值，算出 EXP/hr 與升級剩餘時間。不用安裝程式，也完全不接觸遊戲程序：
不注入、不讀記憶體、不攔封包，只看分享出來的畫面。

這個 Repo 從 [MapleExpTool](https://github.com/andy971029/MapleExpTool) 拆出來，只留網頁版。
辨識與追蹤邏輯和桌面版是同一套 Python 程式碼，用 [Pyodide](https://pyodide.org/) 在瀏覽器裡執行。

## 使用

直接用 Chrome 或 Edge 開 https://andy971029.github.io/PrisonDiary/ ，什麼都不用裝。
每次 push 到 main，GitHub Actions 會先跑測試，通過才重新打包並發布。

### 在自己電腦跑

需要 Python 3.11 以上（只用來打包與開本機伺服器）和 Chrome 或 Edge。

```powershell
git clone https://github.com/andy971029/PrisonDiary.git
cd PrisonDiary
.\web\serve.cmd
```

1. 用 Chrome 或 Edge 開 http://localhost:8765/ 。
2. 等按鈕從「載入 Python 環境…」變成「分享遊戲視窗」。第一次要下載約 10 MB 的 Pyodide 與 numpy，之後瀏覽器會快取。
3. 按「分享遊戲視窗」，在對話框選「視窗」分頁、挑遊戲視窗。
4. 想邊玩邊看，就按「開啟小浮窗」。

## 功能

- **經驗值與速率：** 用字元模板比對讀經驗值，不用 OCR。實測 OCR 讀數字幾乎全錯。
- **角色、職業、等級、地圖：** 用 Tesseract.js 辨識。中文走 `chi_tra`，等級的數字走英文模型。語言檔第一次要下載幾十 MB，之後存在瀏覽器的 IndexedDB。
- **歷史紀錄：** 以「角色 × 地圖」為一段，結算時確認才存進瀏覽器的 localStorage。只存在這個瀏覽器，可以匯出 JSON。
- **經驗值 1.5 倍試算：** 速率、累計與倒數都以 1.5 倍顯示，不影響歷史紀錄。
- **小浮窗：** Chrome / Edge 會開成一直置頂的小視窗，可以疊在遊戲上。它只複製主頁面已經顯示的文字，不另外計算。上方的圖示按鈕可以停止、暫停、重置、縮小或關閉。縮小後只留狀態燈、速率與升級倒數。其他瀏覽器會退回一般彈出視窗，不會置頂。

讀不到數字時，展開頁面最下方的「診斷」，會放大顯示程式實際看到的像素。

## 結構

```
web/
├── index.html        介面、歷史紀錄、小浮窗（JS）
├── bridge.py         在 Pyodide 裡跑的膠水層：canvas 畫面 → 辨識 → 追蹤 → JSON
├── mapvocab.json     地圖名稱清單快照，用來校正 OCR 讀到的地圖名
└── serve.cmd         打包 + 開本機伺服器
src/mapleexp/
├── vision/           二值化、切割、模板比對、定位、面板與身分辨識
├── core/             讀值解析、追蹤狀態機、統計、經驗表反推
├── win32/capture.py  只有型別的替身；真正的擷取由瀏覽器的 getDisplayMedia 做
└── builtin_templates.json   內建字元模板
scripts/
├── build_web.py      把 src/mapleexp 打包成 web/mapleexp.zip
└── serve_web.py      送 no-store 的靜態伺服器，改了檔案重新整理就生效
```

## 測試

測試都不需要遊戲或瀏覽器：視覺管線用 `src/mapleexp/testfont.py` 的點陣字型合成畫面，
`tests/test_web_bridge.py` 在一般 CPython 下直接匯入 `bridge.py` 驗證。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m unittest discover -s tests -t tests
```

`tests/` 不是套件，`tests/helpers.py` 負責把 `src` 放進 `sys.path`。

## 授權

MIT
