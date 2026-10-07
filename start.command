#!/bin/bash
# ProfitDesk launcher for Mac and Linux.
# Double-click this file. That's it.

cd "$(dirname "$0")" || exit 1
clear

echo ""
echo "  ProfitDesk"
echo "  ----------"
echo ""

# --- find Python -----------------------------------------------------------
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "  Python isn't installed on this computer."
  echo ""
  echo "  I'll open the download page. Install it, then double-click"
  echo "  this file again."
  echo ""
  echo "  On the installer, tick 'Add Python to PATH' if you see it."
  echo ""
  (open https://www.python.org/downloads/ 2>/dev/null || \
   xdg-open https://www.python.org/downloads/ 2>/dev/null) &
  read -r -p "  Press Enter to close. "
  exit 1
fi

# --- install the pieces it needs (first run only, ~30 seconds) -------------
echo "  Checking the parts it needs..."
$PY -m pip install --quiet -r requirements.txt 2>/dev/null \
  || $PY -m pip install --quiet --user -r requirements.txt 2>/dev/null \
  || $PY -m pip install --quiet --break-system-packages -r requirements.txt 2>/dev/null

if ! $PY -c "import fastapi, uvicorn, httpx, dotenv" >/dev/null 2>&1; then
  echo ""
  echo "  Something went wrong installing the parts."
  echo "  Copy this whole window and send it to Claude."
  echo ""
  $PY -m pip install -r requirements.txt
  read -r -p "  Press Enter to close. "
  exit 1
fi

# --- settings file ---------------------------------------------------------
[ -f .env ] || cp .env.example .env

# --- offer demo data on first run -----------------------------------------
if [ ! -f profitdesk.db ]; then
  echo ""
  echo "  First time running this."
  echo ""
  echo "    [1]  Load pretend data so I can look around first"
  echo "    [2]  Connect my real Shopify store"
  echo ""
  read -r -p "  Type 1 or 2, then press Enter: " choice
  if [ "$choice" = "1" ]; then
    echo "  Making some pretend data..."
    $PY seed_demo.py >/dev/null 2>&1
  fi
fi

# --- go --------------------------------------------------------------------
echo ""
echo "  Starting up. Your browser will open in a moment."
echo ""
echo "  KEEP THIS WINDOW OPEN while you use ProfitDesk."
echo "  To stop it, close this window."
echo ""

( sleep 4
  open http://127.0.0.1:8787 2>/dev/null || \
  xdg-open http://127.0.0.1:8787 2>/dev/null ) &

$PY app.py

echo ""
read -r -p "  ProfitDesk stopped. Press Enter to close. "
