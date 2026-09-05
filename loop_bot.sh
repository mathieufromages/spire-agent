#!/usr/bin/env bash
# Run Spire Agent back-to-back forever (random seed each run).
#   ./loop_bot.sh [--character IRONCLAD --ascension 0]
# Stop with: touch ~/spire-agent/STOP   (or pkill -f loop_bot.sh)
cd "$(dirname "$(readlink -f "$0")")"
# Unattended runs go through gamescope headless by default so a powered-down
# monitor cannot kill the game (see run_bot.sh). SPIRE_HEADLESS=0 to watch.
export SPIRE_HEADLESS="${SPIRE_HEADLESS:-1}"
LOG_DIR=logs; mkdir -p "$LOG_DIR"
rm -f STOP
n=0
fails=0
while [[ ! -f STOP ]]; do
  n=$((n+1))
  ts=$(date +%Y%m%d-%H%M%S)
  echo "=== run $n starting $ts ===" | tee -a "$LOG_DIR/loop.log"
  ./run_bot.sh --no-tui "$@" > "$LOG_DIR/run-$ts.log" 2>&1
  rc=$?
  last=$(grep -E '^\[[0-9]+\]' "$LOG_DIR/run-$ts.log" | tail -1)
  echo "=== run $n ended rc=$rc :: $last" | tee -a "$LOG_DIR/loop.log"
  # The game overwrites its own logs on every launch; keep this run's copies.
  cp -f runtime/out/stderr.log "$LOG_DIR/run-$ts.game-stderr.log" 2>/dev/null
  cp -f runtime/tmp/sendToDevs/logs/SlayTheSpire.log "$LOG_DIR/run-$ts.game.log" 2>/dev/null
  # Stop retrying when the game itself cannot start (no display, broken mods):
  # three consecutive failures with no action executed means a systemic fault.
  if [[ $rc -ne 0 && -z "$last" ]]; then
    fails=$((fails+1))
    if [[ $fails -ge 3 ]]; then
      echo "=== $fails consecutive runs failed before the first action; stopping the loop" | tee -a "$LOG_DIR/loop.log"
      break
    fi
  else
    fails=0
  fi
  pkill -f ModTheSpire 2>/dev/null; sleep 5
done
echo "STOP file seen, exiting" | tee -a "$LOG_DIR/loop.log"
