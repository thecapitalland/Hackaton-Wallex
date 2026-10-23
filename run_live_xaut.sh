#!/bin/bash
# Watchdog for live-xaut. Usage: ./run_live_xaut.sh [--execute]   (real orders need --execute AND LIVE=1 in env)
cd "$(dirname "$0")"; export PYTHONUNBUFFERED=1
FLAG=logs/live_xaut.DONE; [ "$1" != "--execute" ] && FLAG=logs/live_xaut.DONE.dry
while [ ! -f "$FLAG" ]; do
  .venv/bin/python main.py live-xaut "$@" --sleep 15 >> logs/live_xaut_run.log 2>&1
  [ -f "$FLAG" ] && break; echo "$(date) live-xaut exited, restarting in 10s" >> logs/live_xaut_run.log; sleep 10
done
echo "$(date) live-xaut finished (DONE flag present)" >> logs/live_xaut_run.log
