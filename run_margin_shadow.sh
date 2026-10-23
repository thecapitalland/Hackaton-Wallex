#!/bin/bash
# Watchdog for margin-gap SHADOW (never opens real margin positions). Usage: ./run_margin_shadow.sh
cd "$(dirname "$0")"; export PYTHONUNBUFFERED=1; unset MARGIN_LIVE
while true; do
  .venv/bin/python main.py margin-gap --sleep 15 >> logs/margin_shadow_run.log 2>&1
  echo "$(date) margin-gap exited, restarting in 10s" >> logs/margin_shadow_run.log; sleep 10
done
