@echo off
REM Windows: double-click me to start the dashboard.
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python 3.10+ is required - install from https://www.python.org/downloads/
  echo (check "Add python.exe to PATH" during install^)
  pause
  exit /b 1
)
%PY% -c "import flask, requests" >nul 2>nul || (
  echo Installing dependencies...
  %PY% -m pip install --user --quiet flask requests
)
if not defined PORT set PORT=5177
echo Dashboard: http://localhost:%PORT%  (close this window to stop^)
start "" http://localhost:%PORT%
%PY% app.py
pause
