@echo off
rem Double-click after the game client updates: re-exports every book from the
rem client, shows what changed, runs the tests, then asks before publishing.
rem Keep this file ASCII-only: cmd.exe reads .bat with the ANSI codepage,
rem so non-ASCII text gets mangled. The messages live in scripts\update_data.py.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo .venv not found. Run these first:
  echo   python -m venv .venv
  echo   .venv\Scripts\python -m pip install -r requirements.txt UnityPy
  pause
  exit /b 1
)
"%PY%" scripts\update_data.py
set "CODE=%ERRORLEVEL%"
echo.
pause
endlocal & exit /b %CODE%
