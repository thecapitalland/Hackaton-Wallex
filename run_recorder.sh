#!/bin/bash
# supervised: restart on crash
cd "$(dirname "$0")"
while true; do .venv/bin/python -u recorder.py >> logs/recorder.log 2>&1; echo "$(date) recorder exited, restarting" >> logs/recorder.log; sleep 5; done
