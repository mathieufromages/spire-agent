"""Fixed card value tables for removal, upgrade, and pick decisions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import re

from spire_agent.tools.sts_db import StsDB


UNREMOVABLE = frozenset({"ascender's bane", "curse of the bell", "necronomicurse"})

# Higher means "upgrade this first".  Unlisted cards fall back to a
# rarity/type default so both supported characters stay covered.
_UPGRADE_PRIORITY = {
    # Ironclad
    "demon form": 95, "barricade": 94, "corruption": 92, "feel no pain": 90,
    "dark embrace": 88, "limit break": 88, "inflame": 86, "metallicize": 84,
    "offering": 84, "battle trance": 82, "shrug it off": 80, "shockwave": 80,
    "whirlwind": 78, "immolate": 78, "reaper": 78, "impervious": 77,
    "disarm": 76, "pommel strike": 76, "uppercut": 75, "bludgeon": 74,
    "carnage": 74, "fiend fire": 74, "second wind": 73, "power through": 72,
    "armaments": 72, "headbutt": 70, "body slam": 70, "flame barrier": 70,
    "ghostly armor": 68, "true grit": 68, "iron wave": 66, "twin strike": 64,
    "cleave": 64, "clothesline": 64, "sword boomerang": 62, "perfected strike": 62,
    "heavy blade": 62, "anger": 60, "thunderclap": 60, "wild strike": 55,
    "juggernaut": 82, "berserk": 80, "double tap": 76, "feed": 72, "entrench": 72,
    "exhume": 70, "spot weakness": 66, "seeing red": 66, "blood for blood": 66,
    "burning pact": 62, "hemokinesis": 62, "rage": 62, "evolve": 62, "rampage": 60,
    "bloodletting": 60, "combust": 60, "sever soul": 60, "dual wield": 60,
    "dropkick": 58, "fire breathing": 58, "rupture": 55, "searing blow": 50,
    "sentinel": 50, "infernal blade": 45, "flex": 45, "intimidate": 40, "blind": 40,
    "warcry": 40, "reckless charge": 40, "havoc": 30, "clash": 30,
    "bash": 40,
    # Defect
    "echo form": 96, "defragment": 92, "glacier": 90, "coolheaded": 88,
    "compile driver": 86, "loop": 86, "capacitor": 84, "buffer": 84,
    "biased cognition": 84, "creative ai": 82, "machine learning": 82,
    "hyperbeam": 80, "sunder": 80, "ball lightning": 78, "cold snap": 78,
    "charge battery": 76, "skim": 76, "reinforced body": 74, "self repair": 74,
    "static discharge": 72, "storm": 72, "heatsinks": 72, "electrodynamics": 72,
    "multi-cast": 70, "rainbow": 70, "seek": 70, "meteor strike": 68,
    "blizzard": 68, "thunder strike": 66, "reprogram": 60, "turbo": 58,
    "zap": 50, "dualcast": 50,
    # Colorless
    "apotheosis": 95, "master of strategy": 80, "panache": 60, "swift strike": 55,
    "finesse": 55, "flash of steel": 55,
    # Basics
    "defend": 12, "strike": 8,
}

# Higher means "remove this first".
_REMOVAL_PRIORITY = {
    "strike": 60, "defend": 50, "bash": 20, "zap": 20, "dualcast": 18,
    "clash": 30, "wild strike": 26, "sword boomerang": 12, "twin strike": 16,
    "searing blow": 24, "warcry": 22, "rampage": 18, "havoc": 20,
    "true grit": 14, "sentinel": 14, "flex": 18, "body slam": 14,
    "leap": 22, "beam cell": 20, "stack": 20, "steam barrier": 18,
    "go for the eyes": 20, "sweeping beam": 14,
}

_STARTER_NAMES = frozenset({"strike", "defend", "bash", "zap", "dualcast"})

# Cards whose value as a *new pick* differs from their upgrade priority: cheap
# damage that is fine to upgrade when already owned but not worth a deck slot
# once the deck has its attacks.
_PICK_VALUE = {
    "bash": 20, "wild strike": 45, "anger": 50, "perfected strike": 50,
    "sword boomerang": 52, "thunderclap": 55, "twin strike": 56, "headbutt": 58,
    "clothesline": 58, "true grit": 60, "cleave": 60, "iron wave": 60,
    "body slam": 62, "pommel strike": 72,
    "zap": 20, "dualcast": 20,
}

# Powers whose second copy is (nearly) dead.  Every other power may be picked
# twice; no card may be picked more than three times.
_SINGLE_COPY_POWERS = frozenset({
    "barricade", "corruption", "juggernaut", "evolve", "fire breathing",
    "combust", "rupture", "berserk", "brutality", "dark embrace", "metallicize",
    "echo form", "creative ai", "machine learning", "static discharge", "storm",
    "heatsinks", "electrodynamics", "buffer", "loop", "capacitor",
})
_STACKING_POWERS = frozenset({"demon form", "inflame", "feel no pain", "defragment", "biased cognition"})
_MAX_COPIES = 3

# Heart-relevant cores (2026-09-05, 8 Act 4 decks): every Heart kill had a
# scaling core plus a finisher; both decks with neither died.  Tier S cards
# are taken over any non-core pick when not yet owned; tier A finishers are
# taken from act 2 on when the deck has none.
HEART_SCALING = frozenset({
    "limit break", "demon form", "corruption", "feel no pain", "dark embrace",
    "barricade", "apotheosis",
})
HEART_FINISHERS = frozenset({
    "reaper", "fiend fire", "immolate", "whirlwind", "bludgeon", "offering",
    "shockwave", "disarm",
})
_TRUE_FINISHERS = frozenset({"reaper", "fiend fire", "immolate", "whirlwind", "bludgeon"})
# Cards that become a scaling core once the deck owns one of the listed
# enablers (Juggernaut with Barricade: 174 block a turn and still lost the
# Heart damage race in run 1RNKX1FUYADUD after skipping it on floor 46).
_SYNERGY_SCALING = {"juggernaut": frozenset({"barricade"})}
# Finishers that only work with an enabler in the deck.  Heavy Blade with
# Demon Form or Limit Break is the Heart's damage race in one card; strength
# decks skipped it 9 times in the recorded runs and lost the race in 5.
_SYNERGY_FINISHERS = {"heavy blade": frozenset({"demon form", "limit break"})}
_SYNERGY_FINISHER_BONUS = 30.0

# Soft deck-size caps per act (physical cards, curses included).  Above the cap
# only cards at or above the paired pick value are worth a slot; above the hard
# cap only top-tier cards are.
_DECK_CAPS = {1: (18, 60.0), 2: (22, 62.0), 3: (25, 66.0), 4: (26, 70.0)}
_HARD_CAP = (30, 76.0)


def normalize(name: object) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().casefold())


def base_name(name: object) -> str:
    """Strip the upgrade suffix (``Card+`` / ``Card+2``)."""

    return re.sub(r"\+\d*$", "", str(name or "").strip())


def card_facts(name: object) -> dict[str, object]:
    row = StsDB().card(base_name(name))
    if row is None:
        return {"rarity": "", "type": ""}
    return {"rarity": str(row.get("rarity") or ""), "type": str(row.get("type") or "")}


def is_curse(name: object, card_type: object = None) -> bool:
    kind = normalize(card_type) if card_type else normalize(card_facts(name).get("type"))
    return kind == "curse"


def upgrade_value(name: object) -> float:
    key = normalize(base_name(name))
    if key in _UPGRADE_PRIORITY:
        return float(_UPGRADE_PRIORITY[key])
    facts = card_facts(name)
    kind, rarity = normalize(facts["type"]), normalize(facts["rarity"])
    if kind in {"curse", "status"} or not kind:
        return -1.0
    base = {"power": 70.0, "attack": 50.0, "skill": 52.0}.get(kind, 45.0)
    bonus = {"rare": 12.0, "uncommon": 6.0, "common": 0.0, "basic": -30.0}.get(rarity, 0.0)
    return base + bonus


def removal_value(name: object) -> float:
    """How much removing ``name`` helps; negative means keep it."""

    label = str(name or "")
    key = normalize(base_name(label))
    facts = card_facts(label)
    kind, rarity = normalize(facts["type"]), normalize(facts["rarity"])
    if key in UNREMOVABLE:
        return -1000.0
    if kind == "curse" or rarity == "curse":
        return 100.0
    if kind == "status":
        return 90.0
    value = float(_REMOVAL_PRIORITY.get(key, 0.0))
    if value == 0.0:
        value = {"basic": 30.0, "common": 6.0, "uncommon": 2.0, "rare": -10.0}.get(rarity, 4.0)
        if kind == "power":
            value -= 10.0
    if label.rstrip().endswith("+") or re.search(r"\+\d*$", label.rstrip()):
        value -= 8.0
    return value


def pick_value(name: object) -> float:
    """Rough desirability of adding ``name`` to the deck."""

    key = normalize(base_name(name))
    if key in _PICK_VALUE:
        return float(_PICK_VALUE[key])
    return upgrade_value(name)


def pick_veto(deck: object, name: object, act: object = 1) -> str | None:
    """Return why ``name`` should not join ``deck`` now, or None if it may.

    Applied on top of the Winning Path picker: it stops duplicate powers,
    fourth copies and late-run filler that bloat the deck (2026-09-05: the
    Heart losses carried 30-33 cards with few upgrades and no scaling).
    """

    key = normalize(base_name(name))
    if not key:
        return None
    facts = card_facts(name)
    kind = normalize(facts["type"])
    rows = deck_rows(deck)
    copies = sum(1 for row in rows if normalize(row["name"]) == key)
    if kind == "power" and key not in _STACKING_POWERS:
        limit = 1 if key in _SINGLE_COPY_POWERS else 2
        if copies >= limit:
            return f"already own {copies} {base_name(name)} (power limit {limit})"
    if copies >= _MAX_COPIES:
        return f"already own {copies} copies of {base_name(name)}"
    value = pick_value(name)
    size = len(rows)
    hard_size, hard_value = _HARD_CAP
    if size >= hard_size and value < hard_value:
        return f"deck has {size} cards; {base_name(name)} ({value:.0f}) is below the hard cap {hard_value:.0f}"
    cap_size, cap_value = _DECK_CAPS.get(max(1, min(4, _int(act))), _DECK_CAPS[4])
    if size >= cap_size and value < cap_value:
        return f"deck has {size} cards in act {_int(act)}; {base_name(name)} ({value:.0f}) is filler"
    return None


def core_preference(deck: object, offered: Sequence[object], current: object, act: object = 1) -> tuple[int, str] | None:
    """Return (index, reason) of an offered Heart-core card that should replace
    ``current`` (the picker's choice name, or None for skip), else None."""

    rows = deck_rows(deck)
    owned = {normalize(row["name"]) for row in rows}
    cur = normalize(base_name(current)) if current else ""
    if cur in HEART_SCALING or cur in HEART_FINISHERS:
        return None
    cur_value = pick_value(current) if current else 0.0
    has_finisher = bool(owned & _TRUE_FINISHERS)
    best: tuple[float, int, str] | None = None
    for index, name in enumerate(offered):
        key = normalize(base_name(name))
        if pick_veto(deck, name, act) is not None:
            continue
        synergy = bool(owned & _SYNERGY_SCALING.get(key, frozenset()))
        if (key in HEART_SCALING or synergy) and key not in owned:
            reason = (
                f"{base_name(name)} scales with {', '.join(sorted(owned & _SYNERGY_SCALING[key]))}"
                if synergy and key not in HEART_SCALING
                else f"{base_name(name)} is a missing scaling core"
            )
            score = pick_value(name) + 100.0
        elif (
            key in _SYNERGY_FINISHERS
            and key not in owned
            and owned & _SYNERGY_FINISHERS[key]
            and pick_value(name) + _SYNERGY_FINISHER_BONUS >= cur_value + 4
        ):
            reason = f"{base_name(name)} scales with {', '.join(sorted(owned & _SYNERGY_FINISHERS[key]))}"
            score = pick_value(name) + _SYNERGY_FINISHER_BONUS
        elif (
            key in HEART_FINISHERS
            and key not in owned
            and _int(act) >= 2
            and (not has_finisher or key in _TRUE_FINISHERS and pick_value(name) >= cur_value + 10)
            and pick_value(name) >= cur_value + 4
        ):
            reason = f"{base_name(name)} is a finisher the deck lacks" if not has_finisher else f"{base_name(name)} outranks {base_name(current)}"
            score = pick_value(name)
        else:
            continue
        if best is None or score > best[0]:
            best = (score, index, reason)
    if best is None:
        return None
    return best[1], best[2]


def deck_rows(deck: object) -> list[dict[str, object]]:
    """Return one row per physical deck card with name/upgraded/type flags."""

    rows = []
    for item in deck if isinstance(deck, Sequence) and not isinstance(deck, (str, bytes)) else ():
        if isinstance(item, Mapping):
            raw = str(item.get("name") or item.get("id") or "")
            count = max(1, _int(item.get("count", 1)))
            upgrades = _int(item.get("upgrades", item.get("upgrade", 0)))
            kind = str(item.get("type") or "")
        else:
            raw, count, upgrades, kind = str(item), 1, 0, ""
        if not raw:
            continue
        name = base_name(raw)
        suffix_upgraded = raw.rstrip().endswith("+") or bool(re.search(r"\+\d+$", raw.rstrip()))
        upgraded_copies = min(count, max(upgrades, int(suffix_upgraded) * count))
        for index in range(count):
            rows.append(
                {
                    "name": name,
                    "upgraded": index < upgraded_copies,
                    "type": kind or str(card_facts(name).get("type") or ""),
                }
            )
    return rows


# Cards that block on their own; a deck with fewer than _MIN_BLOCK_SOURCES of
# them keeps its Defends (4 recorded decks reached Act 3 with no Defend and
# 3 of them died; 24I4U0C8BFNH8 lost 23-30 HP per hallway fight).
_BLOCK_SOURCES = frozenset({
    "shrug it off", "iron wave", "true grit", "second wind", "impervious",
    "flame barrier", "ghostly armor", "power through", "entrench", "sentinel",
    "armaments", "metallicize", "leap", "steam barrier", "hologram", "glacier",
    "reinforced body", "boot sequence", "chill", "auto-shields", "stack",
    "genetic algorithm", "coolheaded", "charge battery", "reboot",
})
_MIN_BLOCK_SOURCES = 4


def removal_targets(deck: object, count: int = 1, *, exclude: Iterable[str] = ()) -> list[str]:
    """Return up to ``count`` base card names, worst first, that may be removed."""

    skip = {normalize(item) for item in exclude}
    rows = deck_rows(deck)
    block_sources = sum(1 for row in rows if normalize(row["name"]) in _BLOCK_SOURCES)
    keep_defends = block_sources < _MIN_BLOCK_SOURCES
    ranked = sorted(
        (
            (
                removal_value(row["name"] + ("+" if row["upgraded"] else ""))
                - (40.0 if keep_defends and normalize(row["name"]) == "defend" else 0.0),
                row["name"],
            )
            for row in rows
            if normalize(row["name"]) not in UNREMOVABLE and normalize(row["name"]) not in skip
        ),
        key=lambda item: (-item[0], item[1]),
    )
    return [name for _value, name in ranked[:count]]


def upgrade_targets(deck: object, count: int = 1) -> list[str]:
    """Return up to ``count`` un-upgraded base card names, best first."""

    seen: dict[str, int] = {}
    ranked = []
    for row in deck_rows(deck):
        if row["upgraded"] or is_curse(row["name"], row["type"]):
            continue
        value = upgrade_value(row["name"])
        if value < 0:
            continue
        ranked.append((value, row["name"]))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    result = []
    for _value, name in ranked:
        seen[name] = seen.get(name, 0) + 1
        result.append(name)
        if len(result) >= count:
            break
    return result


def has_removal_target(deck: object, *, minimum: float = 20.0) -> bool:
    names = removal_targets(deck, 1)
    return bool(names) and removal_value(names[0]) >= minimum


def is_starter(name: object) -> bool:
    return normalize(base_name(name)) in _STARTER_NAMES


def _int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "HEART_FINISHERS",
    "HEART_SCALING",
    "UNREMOVABLE",
    "base_name",
    "card_facts",
    "core_preference",
    "deck_rows",
    "has_removal_target",
    "is_curse",
    "is_starter",
    "normalize",
    "pick_value",
    "pick_veto",
    "removal_targets",
    "removal_value",
    "upgrade_targets",
    "upgrade_value",
]
