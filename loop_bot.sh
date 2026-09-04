#!/usr/bin/env bash
# Run Spire Agent back-to-back forever (random seed each run).
#   ./loop_bot.sh [--character IRONCLAD --ascension 0]
# Stop with: touch ~/spire-agent/STOP   (or pkill -f loop_bot.sh)
cd "$(dirname "$(readlink -f "$0")")"
LOG_DIR=logs; mkdir -p "$LOG_DIR"
rm -f STOP
n=0
while [[ ! -f STOP ]]; do
  n=$((n+1))
  ts=$(date +%Y%m%d-%H%M%S)
  echo "=== run $n starting $ts ===" | tee -a "$LOG_DIR/loop.log"
  ./run_bot.sh --no-tui "$@" > "$LOG_DIR/run-$ts.log" 2>&1
  rc=$?
  last=$(grep -E '^\[[0-9]+\]' "$LOG_DIR/run-$ts.log" | tail -1)
  echo "=== run $n ended rc=$rc :: $last" | tee -a "$LOG_DIR/loop.log"
  pkill -f ModTheSpire 2>/dev/null; sleep 5
done
echo "STOP file seen, exiting" | tee -a "$LOG_DIR/loop.log"
