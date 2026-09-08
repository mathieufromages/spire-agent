#!/usr/bin/env bash
# Launch Slay the Spire (modded, own sandboxed copy) + the Spire Agent bot.
#
#   ./run_bot.sh                       # TUI console; type `run` inside it
#   ./run_bot.sh --no-tui --character IRONCLAD --ascension 0 --seed 0
#
# The .env next to this script supplies MODEL_URL / MODEL / API_KEY and
# STS_JRE_DIR. It is gitignored; never commit it.
#
# SPIRE_RUNTIME_DIR=runtime2 ./run_bot.sh ...   uses a second game sandbox
# (<dir>/lib, <dir>/mods copied from runtime/) so two bots can play at once.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
RUNTIME_DIR="${SPIRE_RUNTIME_DIR:-runtime}"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

# SPIRE_NO_LLM=1 ./run_bot.sh ... drops the LLM credentials sourced from .env
# so a run provably uses only the rule-based agents (config.yaml agents:
# map/build heuristic, combat mcts). With an llm/winning_path agent selected
# the bot then refuses to start instead of silently calling the model.
if [[ -n "${SPIRE_NO_LLM:-}" ]]; then
  unset API_KEY MODEL_URL MODEL
fi

# Steam integration: the sandboxed game (runtime/tmp) is not launched by Steam,
# so SteamAPI_Init() needs steam_appid.txt in its working directory. gym-sts
# copies runtime/lib/* into the sandbox on every launch, so the file lives
# there. With Steam running this gives "Playing Slay the Spire", rich presence
# and (with the AchievementEnabler mod in config.yaml run.extra_mods) real
# achievements on your account. SPIRE_STEAM=0 disables it.
if [[ "${SPIRE_STEAM:-1}" == "0" ]]; then
  rm -f "$RUNTIME_DIR/lib/steam_appid.txt" "$RUNTIME_DIR/tmp/steam_appid.txt"
else
  mkdir -p "$RUNTIME_DIR/lib"
  printf '646570\n' > "$RUNTIME_DIR/lib/steam_appid.txt"
fi

# Per-instance Java temp dir. steamworks4j re-extracts libsteam_api.so and
# libsteamworks4j.so into <java.io.tmpdir>/steamworks4j/<version>/ on every
# game start, overwriting files another running game has mapped; the game
# attached to Steam then dies with SIGSEGV in SteamTicker.run() (2026-09-08,
# every launch of a second instance killed the first). libgdx natives live
# there too. The JVM ignores TMPDIR, so use JAVA_TOOL_OPTIONS.
mkdir -p "$RUNTIME_DIR/javatmp"
case "${JAVA_TOOL_OPTIONS:-}" in
  *java.io.tmpdir=*) ;;  # already set (headless re-exec or caller)
  *) export JAVA_TOOL_OPTIONS="-Djava.io.tmpdir=$PWD/$RUNTIME_DIR/javatmp${JAVA_TOOL_OPTIONS:+ $JAVA_TOOL_OPTIONS}" ;;
esac

# Headless mode: SPIRE_HEADLESS=1 runs the bot (and therefore the game it
# spawns) inside gamescope's headless backend, an off-screen GPU-accelerated
# X/Wayland display. Needed for unattended runs: when the physical monitor
# powers down, XWayland reports no display modes and LWJGL dies in
# LinuxDisplay.getAvailableDisplayModes (22 failed loop runs on 2026-09-05).
# The window size follows config.yaml run.window_size (default 1600x900).
if [[ "${SPIRE_HEADLESS:-0}" == "1" && -z "${SPIRE_HEADLESS_INNER:-}" ]]; then
  size=$(grep -E '^\s*window_size:' config.yaml | head -1 | sed -E 's/.*:\s*([0-9]+)x([0-9]+).*/\1 \2/')
  read -r gs_w gs_h <<< "${size:-1600 900}"
  [[ "$gs_w" =~ ^[0-9]+$ && "$gs_h" =~ ^[0-9]+$ ]] || { gs_w=1600; gs_h=900; }
  export SPIRE_HEADLESS_INNER=1
  exec gamescope --backend headless -W "$gs_w" -H "$gs_h" -- "$0" "$@"
fi

if [[ -n "${SPIRE_RUNTIME_DIR:-}" ]]; then
  set -- --runtime-dir "$RUNTIME_DIR" "$@"
fi
exec uv run spire-agent "$@"
