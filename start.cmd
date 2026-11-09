@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  py -3 server.py --open
) else (
  python server.py --open
)
if errorlevel 1 pause
