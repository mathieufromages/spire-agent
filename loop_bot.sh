#!/usr/bin/env bash
# Run Spire Agent back-to-back forever (random seed each run).
#   ./loop_bot.sh [--character IRONCLAD --ascension 0]
# Stop with: touch ~/spire-agent/STOP   (or pkill -f loop_bot.sh)
#
# Several loops can run side by side, each with its own game sandbox:
#   SPIRE_INSTANCE=2 ./loop_bot.sh --character IRONCLAD --ascension 0
# uses runtime2/ (create it once: cp -a runtime/lib runtime/mods runtime2/),
# logs/loop2.log, logs/run2-<ts>.log and stops on STOP or STOP.2.  Game
# launches are serialized by a lock inside the bot (CommunicationMod's config
# file is global), so instances can be started at the same time.
cd "$(dirname "$(readlink -f "$0")")"
# Unattended runs go through gamescope headless by default so a powered-down
# monitor cannot kill the game (see run_bot.sh). SPIRE_HEADLESS=0 to watch.
export SPIRE_HEADLESS="${SPIRE_HEADLESS:-1}"
INSTANCE="${SPIRE_INSTANCE:-}"
if [[ -n "$INSTANCE" ]]; then
  export SPIRE_RUNTIME_DIR="runtime$INSTANCE"
  # Every instance stays attached to Steam (achievements from all of them
  # count): two attached games coexist once each JVM has its own temp
  # directory, which run_bot.sh arranges via JAVA_TOOL_OPTIONS.
  [[ -d "$SPIRE_RUNTIME_DIR/lib" && -d "$SPIRE_RUNTIME_DIR/mods" ]] || {
    echo "missing $SPIRE_RUNTIME_DIR/{lib,mods}; run: cp -a runtime/lib runtime/mods $SPIRE_RUNTIME_DIR/" >&2
    exit 2
  }
fi
RUNTIME_DIR="${SPIRE_RUNTIME_DIR:-runtime}"
LOG_DIR=logs; mkdir -p "$LOG_DIR"
LOOP_LOG="$LOG_DIR/loop$INSTANCE.log"
STOP_FILES=(STOP)
[[ -n "$INSTANCE" ]] && STOP_FILES+=("STOP.$INSTANCE")
rm -f "${STOP_FILES[@]}"

stop_requested() {
  local f
  for f in "${STOP_FILES[@]}"; do [[ -f "$f" ]] && return 0; done
  return 1
}

# Kill only the game that runs from this instance's sandbox (its working
# directory is <runtime>/tmp); another instance's game must survive.
kill_own_game() {
  local tmp pid
  tmp=$(readlink -f "$RUNTIME_DIR/tmp" 2>/dev/null) || return 0
  for pid in $(pgrep -f ModTheSpire); do
    if [[ "$(readlink -f "/proc/$pid/cwd" 2>/dev/null)" == "$tmp" ]]; then
      kill "$pid" 2>/dev/null
    fi
  done
}

n=0
fails=0
while ! stop_requested; do
  n=$((n+1))
  ts=$(date +%Y%m%d-%H%M%S)
  run_log="$LOG_DIR/run$INSTANCE-$ts.log"
  echo "=== run $n starting $ts ===" | tee -a "$LOOP_LOG"
  ./run_bot.sh --no-tui "$@" > "$run_log" 2>&1
  rc=$?
  last=$(grep -E '^\[[0-9]+\]' "$run_log" | tail -1)
  echo "=== run $n ended rc=$rc :: $last" | tee -a "$LOOP_LOG"
  # The game overwrites its own logs on every launch; keep this run's copies.
  cp -f "$RUNTIME_DIR/out/stderr.log" "$LOG_DIR/run$INSTANCE-$ts.game-stderr.log" 2>/dev/null
  cp -f "$RUNTIME_DIR/tmp/sendToDevs/logs/SlayTheSpire.log" "$LOG_DIR/run$INSTANCE-$ts.game.log" 2>/dev/null
  # Stop retrying when the game itself cannot start (no display, broken mods):
  # three consecutive failures with no action executed means a systemic fault.
  if [[ $rc -ne 0 && -z "$last" ]]; then
    fails=$((fails+1))
    if [[ $fails -ge 3 ]]; then
      echo "=== $fails consecutive runs failed before the first action; stopping the loop" | tee -a "$LOOP_LOG"
      break
    fi
  else
    fails=0
  fi
  kill_own_game; sleep 5
done
echo "STOP file seen, exiting" | tee -a "$LOOP_LOG"
