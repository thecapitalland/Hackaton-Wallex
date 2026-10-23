#!/bin/bash
# Watchdog for setup-trade monitor (resume mode via logs/setup_state.json). Stops when logs/setup_trade.DONE exists.
cd "$(dirname "$0")"; export PYTHONUNBUFFERED=1 LIVE=1
while [ ! -f logs/setup_trade.DONE ]; do
  .venv/bin/python main.py setup-trade >> logs/setup_trade_run.log 2>&1
  [ -f logs/setup_trade.DONE ] && break; echo "$(date) setup-trade exited, restarting in 5s" >> logs/setup_trade_run.log; sleep 5
done
