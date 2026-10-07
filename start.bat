@echo off
REM ProfitDesk launcher for Windows. Double-click this file.

cd /d "%~dp0"
cls
echo.
echo   ProfitDesk
echo   ----------
echo.

REM --- find Python ---------------------------------------------------------
set PY=
where python >nul 2>&1 && set PY=python
if "%PY%"=="" (
  where py >nul 2>&1 && set PY=py
)

if "%PY%"=="" (
  echo   Python isn't installed on this computer.
  echo.
  echo   I'll open the download page. Install it, then double-click
  echo   this file again.
  echo.
  echo   IMPORTANT: on the first installer screen, tick the box that
  echo   says "Add Python to PATH" before clicking Install.
  echo.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

REM --- install the pieces it needs (first run only) -------------------------
echo   Checking the parts it needs...
%PY% -m pip install --quiet -r requirements.txt >nul 2>&1
if errorlevel 1 %PY% -m pip install --quiet --user -r requirements.txt >nul 2>&1

%PY% -c "import fastapi, uvicorn, httpx, dotenv" >nul 2>&1
if errorlevel 1 (
  echo.
  echo   Something went wrong installing the parts.
  echo   Copy this whole window and send it to Claude.
  echo.
  %PY% -m pip install -r requirements.txt
  pause
  exit /b 1
)

REM --- settings file --------------------------------------------------------
if not exist .env copy .env.example .env >nul

REM --- offer demo data on first run ----------------------------------------
if not exist profitdesk.db (
  echo.
  echo   First time running this.
  echo.
  echo     [1]  Load pretend data so I can look around first
  echo     [2]  Connect my real Shopify store
  echo.
  set /p choice="  Type 1 or 2, then press Enter: "
  if "%choice%"=="1" (
    echo   Making some pretend data...
    %PY% seed_demo.py >nul 2>&1
  )
)

REM --- go -------------------------------------------------------------------
echo.
echo   Starting up. Your browser will open in a moment.
echo.
echo   KEEP THIS WINDOW OPEN while you use ProfitDesk.
echo   To stop it, close this window.
echo.

start /b cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8787"

%PY% app.py

echo.
pause
