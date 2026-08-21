#!/bin/sh
# Start the audit dashboard from anywhere: path/to/audit/run.sh
# Set PYTHON to override interpreter detection; set PORT to change the port.
cd "$(dirname "$0")"
PORT="${PORT:-5177}"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
  done
fi
if [ -z "$PY" ]; then echo "python3 not found — install Python 3.10+"; exit 1; fi
"$PY" -c "import flask, requests" 2>/dev/null || {
  echo "Missing dependencies — run: $PY -m pip install flask requests"; exit 1; }
EXISTING=$(lsof -ti tcp:$PORT 2>/dev/null)
if [ -n "$EXISTING" ]; then
  echo "Port $PORT busy (PID $EXISTING) — replacing that instance."
  kill $EXISTING 2>/dev/null
  sleep 1
fi
echo "Dashboard: http://localhost:$PORT"
exec "$PY" app.py
