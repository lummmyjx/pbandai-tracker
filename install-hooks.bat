@echo off
REM Turns on the pre-commit guard that blocks committing your Telegram token.
REM Run this once, AFTER the folder has become a git repository.
REM
REM Works with GitHub Desktop as well as command-line git: it copies the hook
REM straight into .git\hooks, so it needs no git command of its own.
cd /d "%~dp0"

if not exist ".git" (
  echo.
  echo This folder is not a git repository yet.
  echo.
  echo   GitHub Desktop:  File -^> Add local repository -^> pick this folder
  echo                    -^> "create a repository" -^> Create repository
  echo   Command line:    git init
  echo.
  echo Then run this file again.
  echo.
  pause
  exit /b 1
)

if not exist "hooks\pre-commit" (
  echo ERROR: hooks\pre-commit is missing from this folder.
  pause
  exit /b 1
)

if not exist ".git\hooks" mkdir ".git\hooks"
copy /Y "hooks\pre-commit" ".git\hooks\pre-commit" >nul
if errorlevel 1 (
  echo ERROR: could not copy the hook into .git\hooks
  pause
  exit /b 1
)

echo.
echo Pre-commit guard enabled.
echo.
echo Any commit that would include config.json, a .env file, a debug/ page dump,
echo or anything shaped like a Telegram bot token will now be refused - whether
echo you commit from GitHub Desktop or the command line.
echo.
echo Re-run this file if you ever move or re-clone the folder.
echo.
pause
