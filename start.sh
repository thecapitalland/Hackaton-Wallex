#!/bin/sh
# Runs the trading loop (paper by default) in background + dashboard in foreground.
set -e
if [ "$MODE" = "live" ]; then
  python main.py live --i-understand-live-risk &
else
  python main.py "${MODE:-paper}" &
fi
exec uvicorn dashboard:app --host 0.0.0.0 --port "${PORT:-8000}"
