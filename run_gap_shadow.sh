#!/bin/bash
# Dry-run (shadow) gap runner for a non-XAUT symbol. Usage: ./run_gap_shadow.sh BTCUSDT   (never places orders)
cd "$(dirname "$0")"; export PYTHONUNBUFFERED=1 GAP_SYMBOL="$1"; unset LIVE
while true; do
  .venv/bin/python main.py live-xaut --sleep 15 >> "logs/live_xaut_${1}_run.log" 2>&1
  [ -f "logs/live_xaut_${1}.DONE.dry" ] && break; sleep 10
done
