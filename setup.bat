@echo off
REM One-time setup for the P-Bandai tracker on Windows 11.
cd /d "%~dp0"

echo.
echo === P-Bandai Tracker setup ===
echo.

py -3 --version >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python was not found.
  echo Install it from https://www.python.org/downloads/  -- tick "Add Python to PATH".
  pause
  exit /b 1
)

echo [1/4] Creating virtual environment...
py -3 -m venv .venv || goto :failed

echo [2/4] Installing Python packages...
call .venv\Scripts\python.exe -m pip install --upgrade pip --quiet || goto :failed
call .venv\Scripts\python.exe -m pip install -r requirements.txt --quiet || goto :failed

echo [3/4] Downloading the headless browser (this one takes a few minutes)...
call .venv\Scripts\python.exe -m playwright install chromium || goto :failed

echo [4/4] Preparing config...
if not exist config.json (
  copy config.example.json config.json >nul
  echo     Created config.json - open it and paste your Telegram bot token and chat id.
) else (
  echo     config.json already exists, leaving it alone.
)

echo.
echo Setup complete. Edit config.json, then run start-tracker.bat
echo.
pause
exit /b 0

:failed
echo.
echo Setup failed. Scroll up for the error, or send it to me and I'll sort it out.
pause
exit /b 1
