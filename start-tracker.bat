@echo off
REM Starts the tracker and opens the dashboard. Leave this window open.
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
  echo Run setup.bat first.
  pause
  exit /b 1
)

title P-Bandai Tracker
.venv\Scripts\python.exe app.py run

echo.
echo Tracker stopped.
pause
