# Combat search tuning, 2026-09-10: the recovery-horizon objective

Why the combat search changed, what evidence backed it, and how to redo or
undo it. Companion to `~/game-ai-plans/STS1_HANDOFF.md` (run-by-run history)
and `STS1_SETUP_LOG.md` (install and build gotchas).

## The problem

`battle-sim` runs a complete-combat MCTS. When that search finds no credible
win anywhere in the tree, `shouldUseRecoverySearch`
(`3rd/sts_lightspeed/include/sim/search/RecoveryRootPolicy.h`) hands the whole
decision to a separate fixed-horizon search
(`runRecoveryHorizonSearch`, `apps/battle-sim.cpp`), which scores a state two
player turns out with `recoverySnapshot`
(`src/sim/search/BattleEvaluator.cpp`):

```
quality = 1.25 * effectiveHp/maxHp
        + 2.00 * enemyProgress
        + 0.25 * clamp(engineScore, -4, 4)
        + 0.02 * potionCount
```

Measured share of baseline searches decided this way over every recorded run:

| room | share |
|---|---|
| Act 4 boss (the Heart) | 49% |
| Act 2 and Act 3 bosses | 14% |
| Act 3 elites | 10% |
| hallways | <= 1% |

So roughly half of every Heart fight is decided by that formula, and it scored
the boundary state without regard to the enemy move **already set** for that
turn. "Alive at 1 HP facing a 40-damage intent" scored almost like "alive at
50 HP". The engine term is clamped and saturates in Heart fights, so it does
not discriminate there either.

Three recorded failures share the shape: Awakened One phase 1 killed at 1-3 HP
then Dark Echo (40 damage) kills on the free rebirth turn; Bronze Automaton
survived at 2 HP through Hyper Beam then dead to the next Flail; the Heart's
big single hits landing on a boundary the search considered fine.

## The change

Submodule `3rd/sts_lightspeed`, local branch `painter/search-tuning`:

| commit | content |
|---|---|
| `1b85053` | `applyRobustContinuationDominance` must preserve the replan boundary (was uncommitted) |
| `cba6644` | `decideRootAction()` / `runPlayout()` split; CLI `playout`, `playout_max_decisions=N`, `rng_offset=N`, `recovery_horizon_turns=N`, `recovery_threat=P,O`; boundary-threat penalty |
| `19dfc56` | clamp `rng_offset`, strict `recovery_threat` parsing |

The penalty, applied only when the horizon is reached with the combat
undecided:

```
threatFrac = getProjectedIncomingHpLoss(bc) / maxHp   # enemy moves already set, minus block
hpFrac     = clamp(effectiveHp / maxHp, 0, 2)
quality   -= P * min(threatFrac, hpFrac)              # pressure: threat the HP term already prices
           + O * max(0, threatFrac - hpFrac)          # overkill: threat HP cannot absorb
```

`P = O = 0` and `recovery_horizon_turns = 2` reproduce the old binary exactly:
verified byte-identical stdout JSON against the pre-change binary on 8 recorded
positions (Act 1 hallway, Act 1 elite, Act 2 hallway, Act 2 boss, a Heart
recovery decision, two potion-authorised searches, a card-select root), with
only the new keys added. `test mcts_regression` passes, including two new
fixtures: a threatened vs harmless boundary at equal HP, and 1 HP vs 50 HP
facing the same lethal Dark Echo.

## The evidence

`scripts/build_playout_positions.py` extracts fight-start states from recorded
searches; `scripts/playout_ab.py` plays each to completion inside the simulator
per arm (`battle-sim ... playout`) and reports paired results. 51 positions
(22 Heart, 29 Act 3 boss), 10k sims/thread, 4 threads, 2 s cap, potions
allowed, one RNG seed:

| arm | wins/51 | Heart | Act 3 boss | paired gained / lost | mean end-HP delta |
|---|---|---|---|---|---|
| base | 31 (62%) | 7/22 | 24/28 | | |
| threat (0.5, 1.5) | 31 (61%) | 6/22 | 25/29 | 1 / 2 | -0.1% |
| h3threat (horizon 3 + same weights) | 34 (67%) | 8/22 | 26/29 | **3 / 1** | +0.5% |

Sign test p = 1.0 on 4 informative pairs: **not significant**. The three gains
were the targeted failure mode (Awakened One from 36% HP; the recorded
phase-2 death, won at 10 HP; a Heart fight won at 29 HP); the one loss was a
fight base had won at 1 HP. Shipped on the direction, not on significance.

Caveat: this budget is ~25x below live, so the recovery fallback fires more
often offline than it does in a real run. Read the paired rows, not the
absolute win rates. A second RNG seed and the elite category were not run.

Raw summary: `docs/search_tuning_2026-09-10.ab.json`.

## What is live

`config.yaml`:

```yaml
paths:
  mcts_binary: 3rd/sts_lightspeed/build-final/battle-sim
mcts:
  recovery_horizon_turns: 3
  recovery_threat: [0.5, 1.5]
```

Every search records its regime in `runs/<seed>/mcts/NNNNNN.json` under
`settings.recovery_horizon_turns` / `settings.recovery_threat`, so tallies can
be split by regime after the fact.

Measured cost on one live Heart fight: recovery search 9.5 s mean and 30 s max
per search (it reaches the 30 s adaptive cap on the big-hit turns) against
0.3-1.9 s at horizon 2; whole-fight search time 18.5 min vs 13.9 min for a
horizon-2 Heart kill the same day, about +33%. Bosses in Acts 2-3 use the
fallback far less, so a full run grows by well under 10 minutes.

## Reproducing or extending

```bash
# fight-start positions by category
uv run python scripts/build_playout_positions.py \
  --categories heart,act3_boss,act2_boss,act4_elite --out /tmp/ab/positions.json

# A/B (first --arms is the paired baseline; keep parallel * threads <= spare cores)
uv run python scripts/playout_ab.py --positions /tmp/ab/positions.json \
  --binary 3rd/sts_lightspeed/build-final/battle-sim \
  --arms 'base:' --arms 'h3threat:recovery_horizon_turns=3 recovery_threat=0.5,1.5' \
  --simulations 10000 --threads-per-playout 4 --parallel 2 --max-time-ms 2000 \
  --seeds 2 --out /tmp/ab/run3
uv run python scripts/playout_ab.py --report-only --out /tmp/ab/run3

# replay one recorded search (bit-identical when it stops on simulation_budget)
uv run python scripts/replay_search.py runs/<seed>/mcts/000513.json \
  --binary 3rd/sts_lightspeed/build-final/battle-sim --threads 4 --max-time-ms 600000
```

Playouts run at most `--parallel * --threads-per-playout` threads; two live bot
loops already use 16 on this box, so run A/Bs only with a loop stopped or the
counts reduced.

## Rolling back

- Weights only: comment out `recovery_threat` in `config.yaml` (binary default
  is `0,0`, i.e. no penalty).
- Horizon only: `recovery_horizon_turns: 2` keeps the penalty and drops the
  time cost.
- Everything: point `paths.mcts_binary` back at
  `3rd/sts_lightspeed/build/battle-sim` (the 2026-09-03 binary, still in
  place). That binary rejects the two new config keys, so comment them out at
  the same time.

## Building a new binary

Never build into the directory the config points at while a loop is running.

```bash
distrobox enter stsbuild -- bash -c 'cd ~/spire-agent/3rd/sts_lightspeed && \
  cmake -S . -B build-<name> -DCMAKE_BUILD_TYPE=Release && \
  cmake --build build-<name> -j8 --target battle-sim test'
~/spire-agent/3rd/sts_lightspeed/build-<name>/test mcts_regression   # must pass
# then repoint paths.mcts_binary in config.yaml; it takes effect at the next run start
```
