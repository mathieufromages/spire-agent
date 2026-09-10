"""Pattern C: a scaling core / finisher card exhausted or discarded via a
random-hand-attrition tool card, in an elite/boss fight.

Method: walk every run_history entry's (before -> after) combat state pair.
Track cards by uuid. If a card whose base name is in CORE_FINISHER appears in
'before' hand/draw_pile/discard_pile and appears in 'after' exhaust_pile or
discard_pile for the first time (newly moved there) during the same entry
that a TOOL card was played (the entry's action.command played a hand slot
whose 'before' hand card name is in TOOLS), record it.
"""
from __future__ import annotations

import json
from pathlib import Path

import common

CORE_FINISHER = {
    "demon form", "limit break", "corruption", "barricade", "dark embrace",
    "feel no pain", "apotheosis", "juggernaut", "reaper", "fiend fire",
    "whirlwind", "immolate", "heavy blade", "bludgeon", "offering",
}
TOOLS = {
    "elixir", "true grit", "burning pact", "second wind", "sever soul",
    "havoc", "fiend fire",
}


def base_name(name: str) -> str:
    return (name or "").rstrip("+").strip().casefold()


def card_index(cards: list[dict]) -> dict[str, dict]:
    return {c.get("uuid"): c for c in cards if c.get("uuid")}


def played_hand_slot(command: str | None, hand: list[dict]) -> dict | None:
    """For a 'play N' or 'play N T' command, return the hand card at slot N (1-indexed)."""
    if not command or not command.startswith("play "):
        return None
    parts = command.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return None
    idx = int(parts[1]) - 1
    if 0 <= idx < len(hand):
        return hand[idx]
    return None


def analyze(run_dir: Path) -> list[dict]:
    entries = common.load_entries(run_dir)
    outcome = common.run_outcome(run_dir)
    out = []
    for i, e in enumerate(entries):
        before = e.get("before") or {}
        after = e.get("after") or {}
        combat_b = before.get("combat")
        combat_a = after.get("combat")
        if not combat_b or not combat_a:
            continue
        run_b = before.get("run") or {}
        if run_b.get("room_type") not in common.ELITE_BOSS_ROOMS:
            continue
        action = e.get("action") or {}
        command = action.get("command")
        hand_b = combat_b.get("hand") or []
        tool_card = played_hand_slot(command, hand_b)
        if not tool_card or base_name(tool_card.get("name")) not in TOOLS:
            continue

        before_pool = (
            index_cards(combat_b.get("hand"))
            | index_cards(combat_b.get("draw_pile"))
            | index_cards(combat_b.get("discard_pile"))
        )
        after_exhaust = card_index(combat_a.get("exhaust_pile") or [])
        after_discard = card_index(combat_a.get("discard_pile") or [])
        before_exhaust_uuids = {c.get("uuid") for c in (combat_b.get("exhaust_pile") or [])}
        before_discard_uuids = {c.get("uuid") for c in (combat_b.get("discard_pile") or [])}

        for uuid, card in after_exhaust.items():
            if uuid in before_exhaust_uuids or uuid not in before_pool:
                continue
            if base_name(card.get("name")) in CORE_FINISHER:
                out.append(_record(run_dir, before, run_b, tool_card, card, "exhaust", command, action, outcome))
        for uuid, card in after_discard.items():
            if uuid in before_discard_uuids or uuid not in before_pool:
                continue
            # A card discarded by the normal end-of-turn hand-clear is not attrition;
            # only count if the discard happened as part of playing the tool card
            # itself (uuid was in hand before, i.e. hand-thinning effect like
            # Burning Pact/True Grit/Havoc, not natural end-of-turn discard).
            if uuid not in index_cards(combat_b.get("hand")):
                continue
            if base_name(card.get("name")) in CORE_FINISHER:
                out.append(_record(run_dir, before, run_b, tool_card, card, "discard", command, action, outcome))
    return out


def index_cards(cards) -> dict[str, dict]:
    return {c.get("uuid"): c for c in (cards or []) if c.get("uuid")}


def _record(run_dir, before, run_b, tool_card, card, kind, command, action, outcome) -> dict:
    combat_b = before.get("combat") or {}
    monsters = [m.get("name") for m in combat_b.get("monsters", []) if not m.get("is_gone")]
    search_id = None
    if (action.get("decision") or {}).get("source") == "combat.mcts":
        search_id = ((action.get("decision") or {}).get("metrics") or {}).get("search_id")
    mcts_file = str(common.mcts_path(run_dir, search_id)) if search_id else None
    return {
        "seed": run_dir.name,
        "floor": run_b.get("floor"),
        "monsters": monsters,
        "card": card.get("name"),
        "kind": kind,
        "tool": tool_card.get("name"),
        "command": command,
        "outcome": outcome["outcome"],
        "mcts_file": mcts_file,
    }


def main():
    all_records = []
    for d in common.valid_run_dirs():
        all_records.extend(analyze(d))
    for r in all_records:
        print(json.dumps(r))
    print(f"\n# {len(all_records)} core/finisher exhaust-or-discard-via-tool events "
          f"in elite/boss fights across {len({r['seed'] for r in all_records})} seeds")


if __name__ == "__main__":
    main()
