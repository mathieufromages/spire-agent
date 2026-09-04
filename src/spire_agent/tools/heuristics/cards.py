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
    """Rough desirability of adding ``name`` (used for unplanned pick grids)."""

    return upgrade_value(name)


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


def removal_targets(deck: object, count: int = 1, *, exclude: Iterable[str] = ()) -> list[str]:
    """Return up to ``count`` base card names, worst first, that may be removed."""

    skip = {normalize(item) for item in exclude}
    ranked = sorted(
        (
            (removal_value(row["name"] + ("+" if row["upgraded"] else "")), row["name"])
            for row in deck_rows(deck)
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
    "UNREMOVABLE",
    "base_name",
    "card_facts",
    "deck_rows",
    "has_removal_target",
    "is_curse",
    "is_starter",
    "normalize",
    "pick_value",
    "removal_targets",
    "removal_value",
    "upgrade_targets",
    "upgrade_value",
]
