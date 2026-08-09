@echo off
REM Looks for the JSON endpoint behind a P-Bandai listing page.
REM Produces api-report.txt in this folder. Nothing is uploaded anywhere.
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
  echo Run setup.bat first.
  pause
  exit /b 1
)

set "URL=https://p-bandai.com/sg/item/A2884010001"
echo.
echo Which listing should I inspect?
echo Press Enter to use the default, or paste a different P-Bandai URL.
echo   default: %URL%
echo.
set /p "INPUT=URL: "
if not "%INPUT%"=="" set "URL=%INPUT%"

echo.
.venv\Scripts\python.exe find_api.py "%URL%"

echo.
echo Open api-report.txt in this folder and send it over.
pause
