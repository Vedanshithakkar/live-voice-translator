@echo off
rem VoiceBridge launcher: sets itself up on first run, then opens the control panel.
rem Extra arguments are passed through, e.g.  start.bat speak --from en --to gu
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto run

echo First run: creating a Python environment. This takes a few minutes...
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  python -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo Python 3.10 or newer is required. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup, then run this file again.
  pause
  exit /b 1
)
%PY% -m venv .venv || goto fail
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto fail

:run
".venv\Scripts\python.exe" -m voicebridge %*
goto :eof

:fail
echo.
echo Setup failed - see the messages above. Delete the .venv folder and try again.
pause
exit /b 1
