@echo off
rem Double-click after the game client updates: re-exports every book from the
rem client, shows what changed, runs the tests, then asks before publishing.
rem Keep this file ASCII-only: cmd.exe reads .bat with the ANSI codepage,
rem so non-ASCII text gets mangled. The messages live in scripts\update_data.py.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
rem The exports need UnityPy and lz4. A brand-new Python often has no wheels for
rem them yet (3.15 on Windows did not), so a second environment on an older
rem Python, .venv313, is used when it exists; otherwise .venv.
set "PY=.venv313\Scripts\python.exe"
if not exist "%PY%" set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo No Python environment found. Make one that can install UnityPy and lz4:
  echo   uv venv .venv313 --python 3.13
  echo   uv pip install --python .venv313\Scripts\python.exe -r requirements.txt UnityPy Pillow
  echo or, with a Python that has wheels for them:
  echo   python -m venv .venv
  echo   .venv\Scripts\python -m pip install -r requirements.txt UnityPy
  pause
  exit /b 1
)
"%PY%" -c "import UnityPy, lz4" 2>nul
if errorlevel 1 (
  echo %PY% cannot import UnityPy and lz4, so the exports would fail.
  echo Install them into that environment, or create .venv313 as described in the README.
  pause
  exit /b 1
)
"%PY%" scripts\update_data.py
set "CODE=%ERRORLEVEL%"
echo.
pause
endlocal & exit /b %CODE%
