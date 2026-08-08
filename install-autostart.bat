@echo off
REM Registers the tracker to start automatically when you log in to Windows,
REM running silently in the background (no console window).
cd /d "%~dp0"

set TASKNAME=P-Bandai Tracker

schtasks /Create /TN "%TASKNAME%" /TR "wscript.exe \"%~dp0start-hidden.vbs\"" /SC ONLOGON /F /DELAY 0000:30
if errorlevel 1 (
  echo.
  echo Could not create the task. Try running this file as Administrator
  echo ^(right-click -^> Run as administrator^).
  pause
  exit /b 1
)

echo.
echo Done. The tracker will now start ~30 seconds after every login.
echo Dashboard: http://127.0.0.1:8765
echo.
echo To undo this later, run:  schtasks /Delete /TN "%TASKNAME%" /F
echo.
pause
