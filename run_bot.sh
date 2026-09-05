#!/usr/bin/env bash
# Launch Slay the Spire (modded, own sandboxed copy) + the Spire Agent bot.
#
#   ./run_bot.sh                       # TUI console; type `run` inside it
#   ./run_bot.sh --no-tui --character IRONCLAD --ascension 0 --seed 0
#
# The .env next to this script supplies MODEL_URL / MODEL / API_KEY and
# STS_JRE_DIR. It is gitignored; never commit it.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

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
  rm -f runtime/lib/steam_appid.txt runtime/tmp/steam_appid.txt
else
  mkdir -p runtime/lib
  printf '646570\n' > runtime/lib/steam_appid.txt
fi

exec uv run spire-agent "$@"
