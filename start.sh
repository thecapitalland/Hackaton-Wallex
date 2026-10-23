#!/bin/sh
# Liara/Docker entrypoint. MODE: live-xaut (real gap arbitrage), live-xaut-dry, paper.
set -e
# keep logs/state on the persistent disk (/app/data)
mkdir -p /app/data/logs
if [ -d logs ] && [ ! -L logs ]; then cp -rn logs/. /app/data/logs/ 2>/dev/null || true; rm -rf logs; fi
[ -L logs ] || ln -s /app/data/logs logs
case "$MODE" in
  live-xaut)
    LIVE=1 LIQ_AT=${LIQ_AT:-99:00} DECISION_BACKEND=${DECISION_BACKEND:-openai_compat} ./run_live_xaut.sh --execute & ;;
  live-xaut-dry)
    LIQ_AT=${LIQ_AT:-99:00} DECISION_BACKEND=${DECISION_BACKEND:-openai_compat} ./run_live_xaut.sh & ;;
  live)
    python main.py live --i-understand-live-risk & ;;
  *)
    python main.py "${MODE:-paper}" & ;;
esac
exec uvicorn dashboard:app --host 0.0.0.0 --port "${PORT:-8000}"
