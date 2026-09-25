#!/usr/bin/env bash
# ============================================================================
#  Pashu Shield - one-click local run (macOS / Linux)
#
#  Creates .venv if missing, installs backend + ml-backend requirements,
#  then starts BOTH servers:
#    * ML API (FastAPI/uvicorn)  -> http://127.0.0.1:8000
#    * Backend + frontend (Flask) -> http://localhost:5001
#  Finally opens http://localhost:5001 in the default browser.
#
#  Ctrl+C (SIGINT/SIGTERM) shuts both servers down cleanly.
# ============================================================================
set -euo pipefail

# --- resolve the folder this script lives in (handles spaces) ---------------
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo
echo "============================================================"
echo " Pashu Shield - local development startup"
echo " Project folder: $ROOT"
echo "============================================================"
echo

# --- find a Python interpreter ----------------------------------------------
PYTHON_BIN=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON_BIN="$candidate"
    break
  fi
done
if [ -z "$PYTHON_BIN" ]; then
  echo "[ERROR] Python was not found on PATH."
  echo "        macOS:  xcode-select --install   (or brew install python)"
  echo "        Linux : sudo apt install python3-venv python3-pip  (Debian/Ubuntu)"
  exit 1
fi

# --- 1. create .venv if absent -----------------------------------------------
if [ ! -x ".venv/bin/python" ]; then
  echo "[1/4] Creating virtual environment .venv ..."
  "$PYTHON_BIN" -m venv .venv
else
  echo "[1/4] Virtual environment .venv already exists."
fi
VENV_PY="$ROOT/.venv/bin/python"

# --- 2. install dependencies ---------------------------------------------------
echo "[2/4] Installing dependencies (backend + ml-backend requirements) ..."
"$VENV_PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
"$VENV_PY" -m pip install -r "$ROOT/backend/requirements.txt" -r "$ROOT/ml-backend/requirements.txt"

# --- start servers with clean Ctrl+C shutdown ----------------------------------
ML_PID=""
BACKEND_PID=""

cleanup() {
  echo
  echo "Shutting down Pashu Shield servers ..."
  for pid in "$ML_PID" "$BACKEND_PID"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
  echo "Stopped. Bye!"
  exit 0
}
trap cleanup INT TERM

# --- 3. start the ML API (uvicorn on 127.0.0.1:8000) ---------------------------
echo "[3/4] Starting ML API (uvicorn main:app on http://127.0.0.1:8000) ..."
(
  cd "$ROOT/ml-backend"
  exec "$VENV_PY" -m uvicorn main:app --host 127.0.0.1 --port 8000
) &
ML_PID=$!

# --- 4. start the backend + frontend (python app.py on :5001) ------------------
echo "[4/4] Starting backend + frontend (python app.py on http://localhost:5001) ..."
(
  cd "$ROOT/backend"
  exec env PORT=5001 SIH_ML_BACKEND=http://127.0.0.1:8000 "$VENV_PY" app.py
) &
BACKEND_PID=$!

# --- wait for the backend, then open the app ------------------------------------
echo
echo "Waiting for the backend to come up ..."
READY=0
for _ in $(seq 1 30); do
  if curl -fsS "http://localhost:5001/api/health" >/dev/null 2>&1; then
    READY=1
    break
  fi
  # bail out early if a server already crashed
  if ! kill -0 "$BACKEND_PID" 2>/dev/null || ! kill -0 "$ML_PID" 2>/dev/null; then
    break
  fi
  sleep 1
done

if [ "$READY" -eq 1 ]; then
  echo "Backend is up - opening http://localhost:5001 ..."
  if command -v open >/dev/null 2>&1; then
    open "http://localhost:5001" >/dev/null 2>&1 || true        # macOS
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://localhost:5001" >/dev/null 2>&1 || true    # Linux
  else
    echo "(no browser opener found - open http://localhost:5001 manually)"
  fi
else
  echo "WARNING: backend did not report healthy yet; check the logs above."
  echo "         App URL: http://localhost:5001"
fi

echo
echo "============================================================"
echo " Pashu Shield is running:"
echo "   App + API  :  http://localhost:5001"
echo "   ML API     :  http://127.0.0.1:8000  (docs at /docs)"
echo
echo " Press Ctrl+C to stop both servers."
echo "============================================================"

# --- stay attached: if either server dies, stop the other -----------------------
wait -n "$ML_PID" "$BACKEND_PID" 2>/dev/null || true
echo
echo "One of the servers exited unexpectedly - shutting the other down."
cleanup
