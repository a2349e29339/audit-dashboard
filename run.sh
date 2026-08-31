#!/bin/sh
# Start the audit dashboard (macOS/Linux). Double-click "Start Dashboard.command"
# on a Mac, or run this from a terminal. Env: PYTHON overrides the interpreter,
# PORT overrides the port (default 5177).
cd "$(dirname "$0")"
PORT="${PORT:-5177}"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
  done
fi
if [ -z "$PY" ]; then
  echo "Python 3.10+ is required — https://www.python.org/downloads/"; exit 1
fi
if ! "$PY" -c "import flask, requests" 2>/dev/null; then
  echo "Installing dependencies (flask, requests)…"
  "$PY" -m pip install --user --quiet flask requests || {
    echo "Automatic install failed — run: $PY -m pip install flask requests"; exit 1; }
fi
EXISTING=$(lsof -ti tcp:$PORT 2>/dev/null)
if [ -n "$EXISTING" ]; then
  echo "Port $PORT busy (PID $EXISTING) — replacing that instance."
  kill $EXISTING 2>/dev/null
  sleep 1
fi
echo "Dashboard: http://localhost:$PORT  (Ctrl+C to stop)"
( sleep 2
  if command -v open >/dev/null 2>&1; then open "http://localhost:$PORT"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "http://localhost:$PORT"
  fi ) >/dev/null 2>&1 &
exec "$PY" app.py
