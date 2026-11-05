@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 patch.py
) else (
  python patch.py
)
if errorlevel 1 pause
