"""Fixed per-event preferences for the rule-based Build agent.

Each rule receives an ``EventContext`` and returns an ordered tuple of label
markers; the first marker that matches a legal choice wins.  Events that are
not listed fall back to ``DEFAULT_PREFERENCE`` (leave when possible, otherwise
the least risky-looking option).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class EventContext:
    labels: tuple[str, ...]          # normalized choice labels
    texts: tuple[str, ...]           # normalized label + option text
    hp: int
    max_hp: int
    gold: int
    act: int
    floor: int
    ascension: int
    relics: frozenset[str]
    deck_size: int
    basics: int                      # Strikes + Defends still in the deck
    curses: int
    empty_potion_slots: int

    @property
    def ratio(self) -> float:
        return self.hp / self.max_hp if self.max_hp > 0 else 0.0


def normalize(value: object) -> str:
    return re.sub(r"[^a-z0-9+' ]+", " ", str(value or "").casefold()).strip()


_ALIASES: dict[str, tuple[str, ...]] = {
    "neow": ("neow event", "neow"),
    "big_fish": ("big fish",),
    "the_cleric": ("the cleric", "cleric"),
    "dead_adventurer": ("dead adventurer",),
    "golden_idol": ("golden idol",),
    "golden_wing": ("golden wing", "wing statue"),
    "world_of_goop": ("world of goop",),
    "living_wall": ("living wall",),
    "mushrooms": ("mushrooms", "hypnotizing colored mushrooms"),
    "scrap_ooze": ("scrap ooze",),
    "shining_light": ("shining light",),
    "liars_game": ("liars game", "the ssssserpent", "ssssserpent"),
    "accursed_blacksmith": ("accursed blacksmith", "ominous forge"),
    "bonfire": ("bonfire elementals", "bonfire spirits"),
    "wheel": ("wheel of change",),
    "purifier": ("purifier",),
    "transmogrifier": ("transmorgrifier", "transmogrifier"),
    "upgrade_shrine": ("upgrade shrine",),
    "duplicator": ("duplicator",),
    "golden_shrine": ("golden shrine",),
    "lab": ("lab", "the lab"),
    "match_and_keep": ("match and keep", "match and keep!"),
    "note_for_yourself": ("note for yourself",),
    "we_meet_again": ("wemeetagain", "we meet again"),
    "fountain": ("fountain of cleansing", "fountain of purity"),
    "designer": ("designer", "designer in spire"),
    "face_trader": ("facetrader", "face trader"),
    "woman_in_blue": ("the woman in blue", "woman in blue"),
    "addict": ("addict", "pleading vagrant"),
    "back_to_basics": ("back to basics", "ancient writing"),
    "beggar": ("beggar",),
    "colosseum": ("colosseum", "the colosseum"),
    "cursed_tome": ("cursed tome",),
    "drug_dealer": ("drug dealer", "augmenter"),
    "forgotten_altar": ("forgotten altar",),
    "ghosts": ("ghosts", "council of ghosts"),
    "knowing_skull": ("knowing skull",),
    "masked_bandits": ("masked bandits",),
    "nest": ("nest", "the nest"),
    "nloth": ("nloth", "n loth", "n'loth"),
    "the_joust": ("the joust", "joust"),
    "the_library": ("the library", "library"),
    "the_mausoleum": ("the mausoleum", "mausoleum"),
    "vampires": ("vampires",),
    "falling": ("falling",),
    "mind_bloom": ("mindbloom", "mind bloom"),
    "moai_head": ("the moai head", "moai head"),
    "mysterious_sphere": ("mysterious sphere",),
    "secret_portal": ("secretportal", "secret portal"),
    "sensory_stone": ("sensorystone", "sensory stone"),
    "tomb_of_lord_red_mask": ("tomb of lord red mask", "tomb of the lord of red mask"),
    "winding_halls": ("winding halls",),
    "spire_heart": ("spire heart",),
}


def event_key(details: Mapping[str, object]) -> str | None:
    identifiers = {
        normalize(details.get(field))
        for field in ("event_id", "event_name", "event", "name")
        if details.get(field)
    }
    for key, aliases in _ALIASES.items():
        if any(normalize(alias) in identifiers for alias in aliases):
            return key
    return None


def _neow(ctx: EventContext) -> tuple[str, ...]:
    return ("__score:neow",)


def neow_option_score(text: str, ctx: EventContext) -> float:
    """Score one Neow option from its label; positive rewards minus costs."""

    score = 0.0
    gains = (
        ("random common relic", 6.0), ("obtain a random rare card", 5.5),
        ("remove a card", 5.5), ("gain 100 gold", 5.0), ("choose a rare card", 4.5),
        ("choose a card to obtain", 4.0), ("max hp +", 4.0), ("transform a card", 3.5),
        ("obtain 100 gold", 4.8), ("100 gold", 4.8), ("250 gold", 6.0),
        ("upgrade a card", 3.0), ("obtain 3 random potions", 2.5),
        ("random colorless card", 2.0), ("rare colorless", 3.0),
        ("enemies in your next three combats have 1 hp", 1.0),
        ("gain 250 gold", 6.0), ("remove 2 cards", 7.0), ("transform 2 cards", 5.0),
        ("obtain a random rare relic", 6.0), ("choose 1 of 3 random rare cards", 5.5),
        ("max hp +6", 4.0), ("max hp +7", 4.0), ("max hp +8", 4.0),
    )
    for marker, value in gains:
        if marker in text:
            score = max(score, value)
    costs = (
        ("lose all gold", 3.5), ("lose all your gold", 3.5), ("obtain a curse", 4.0),
        ("take damage", 2.0), ("max hp -", 5.5), ("lose your starting relic", 9.0),
        ("no gold", 3.5),
    )
    for marker, value in costs:
        if marker in text:
            score -= value
    if re.search(r"lose \d+ max hp", text):
        score -= 5.5  # the live label is "lose 8 max hp gain 250 gold"
    if "boss relic" in text:
        score -= 2.0
    return score


def _big_fish(ctx: EventContext) -> tuple[str, ...]:
    return ("banana", "donut") if ctx.ratio < 0.7 else ("donut", "banana")


def _cleric(ctx: EventContext) -> tuple[str, ...]:
    purify_cost = 75 if ctx.ascension >= 15 else 50
    order: list[str] = []
    if ctx.gold >= purify_cost and ctx.basics + ctx.curses > 0:
        order.append("purify")
    if ctx.ratio < 0.7 and ctx.gold >= 35:
        order.append("heal")
    order += ["purify", "leave"]
    return tuple(order)


def _dead_adventurer(ctx: EventContext) -> tuple[str, ...]:
    return ("search", "leave") if ctx.ratio >= 0.8 else ("leave", "search")


def _golden_idol(ctx: EventContext) -> tuple[str, ...]:
    if ctx.ratio >= 0.5:
        return ("take", "hide", "outrun", "smash", "leave")
    return ("take", "outrun", "hide", "smash", "leave")


def _golden_wing(ctx: EventContext) -> tuple[str, ...]:
    if ctx.ratio >= 0.4 and ctx.basics + ctx.curses > 0:
        return ("destroy", "pray", "leave")
    return ("destroy", "leave", "pray")


def _goop(ctx: EventContext) -> tuple[str, ...]:
    return ("gather gold", "leave") if ctx.ratio >= 0.5 else ("leave", "gather gold")


def _living_wall(ctx: EventContext) -> tuple[str, ...]:
    if ctx.basics + ctx.curses > 0:
        return ("forget", "grow", "change")
    return ("grow", "forget", "change")


def _mushrooms(ctx: EventContext) -> tuple[str, ...]:
    return ("stomp", "eat") if ctx.ratio >= 0.35 else ("eat", "stomp")


def _scrap_ooze(ctx: EventContext) -> tuple[str, ...]:
    return ("reach inside", "leave") if ctx.ratio >= 0.6 else ("leave", "reach inside")


def _shining_light(ctx: EventContext) -> tuple[str, ...]:
    return ("enter", "leave") if ctx.ratio >= 0.7 else ("leave", "enter")


def _liars_game(ctx: EventContext) -> tuple[str, ...]:
    return ("disagree", "leave", "no")


def _blacksmith(ctx: EventContext) -> tuple[str, ...]:
    return ("forge", "leave", "rummage")


def _bonfire(ctx: EventContext) -> tuple[str, ...]:
    return ("offer",)


def _purifier(ctx: EventContext) -> tuple[str, ...]:
    return ("pray", "leave") if ctx.basics + ctx.curses > 0 else ("leave", "pray")


def _transmogrifier(ctx: EventContext) -> tuple[str, ...]:
    return ("pray", "leave") if ctx.basics + ctx.curses > 0 else ("leave", "pray")


def _upgrade_shrine(ctx: EventContext) -> tuple[str, ...]:
    return ("pray", "leave")


def _duplicator(ctx: EventContext) -> tuple[str, ...]:
    return ("pray", "leave")


def _golden_shrine(ctx: EventContext) -> tuple[str, ...]:
    return ("pray", "leave", "desecrate")


def _lab(ctx: EventContext) -> tuple[str, ...]:
    return ("find", "search", "leave")


def _match_and_keep(ctx: EventContext) -> tuple[str, ...]:
    return ("card0",)


def _note(ctx: EventContext) -> tuple[str, ...]:
    return ("ignore", "leave", "take")


def _we_meet_again(ctx: EventContext) -> tuple[str, ...]:
    order = []
    if ctx.gold >= 150:
        order.append("gold")
    order += ["potion", "card", "attack", "leave"]
    return tuple(order)


def _fountain(ctx: EventContext) -> tuple[str, ...]:
    return ("drink", "leave")


def _designer(ctx: EventContext) -> tuple[str, ...]:
    return ("full service", "clean up", "adjustments", "punch", "leave")


def _face_trader(ctx: EventContext) -> tuple[str, ...]:
    return ("touch", "leave", "trade") if ctx.ratio >= 0.5 else ("leave", "touch", "trade")


def _woman_in_blue(ctx: EventContext) -> tuple[str, ...]:
    if ctx.empty_potion_slots >= 1 and ctx.gold >= 60:
        return ("buy 1 potion", "buy 1", "leave")
    return ("leave", "buy 1")


def _addict(ctx: EventContext) -> tuple[str, ...]:
    return ("offer gold", "leave", "rob") if ctx.gold >= 150 else ("leave", "offer gold", "rob")


def _back_to_basics(ctx: EventContext) -> tuple[str, ...]:
    return ("simplicity", "elegance") if ctx.basics >= 8 else ("elegance", "simplicity")


def _beggar(ctx: EventContext) -> tuple[str, ...]:
    return ("offer gold", "leave") if ctx.gold >= 150 else ("leave", "offer gold")


def _colosseum(ctx: EventContext) -> tuple[str, ...]:
    return ("cowardice", "leave", "victory", "continue", "fight")


def _cursed_tome(ctx: EventContext) -> tuple[str, ...]:
    return ("leave", "stop", "read", "continue", "take")


def _drug_dealer(ctx: EventContext) -> tuple[str, ...]:
    return ("ingest mutagens", "test j.a.x.", "test jax", "become test subject")


def _forgotten_altar(ctx: EventContext) -> tuple[str, ...]:
    if "golden idol" in ctx.relics:
        return ("offer", "sacrifice", "desecrate")
    return ("sacrifice", "desecrate") if ctx.ratio >= 0.6 else ("desecrate", "sacrifice")


def _ghosts(ctx: EventContext) -> tuple[str, ...]:
    return ("refuse", "accept")


def _knowing_skull(ctx: EventContext) -> tuple[str, ...]:
    if ctx.ratio >= 0.9 and ctx.gold < 150:
        return ("gold", "leave")
    return ("leave", "gold")


def _masked_bandits(ctx: EventContext) -> tuple[str, ...]:
    if ctx.ratio >= 0.6 or ctx.gold < 60:
        return ("fight", "pay")
    return ("pay", "fight")


def _nest(ctx: EventContext) -> tuple[str, ...]:
    return ("smash and grab", "gold", "leave")


def _nloth(ctx: EventContext) -> tuple[str, ...]:
    return ("leave", "no thanks")


def _joust(ctx: EventContext) -> tuple[str, ...]:
    return ("owner", "murderer")


def _library(ctx: EventContext) -> tuple[str, ...]:
    return ("read", "sleep") if ctx.ratio >= 0.6 else ("sleep", "read")


def _mausoleum(ctx: EventContext) -> tuple[str, ...]:
    return ("leave", "open")


def _vampires(ctx: EventContext) -> tuple[str, ...]:
    return ("refuse", "accept")


def _falling(ctx: EventContext) -> tuple[str, ...]:
    """Lose the least valuable of the three named cards."""

    from .cards import removal_value

    best: tuple[float, int] | None = None
    for index, text in enumerate(ctx.texts):
        match = re.search(r"lose (.+)$", text)
        if match is None:
            continue
        value = removal_value(match.group(1).strip())
        if best is None or value > best[0]:
            best = (value, index)
    if best is not None:
        return (f"__index:{best[1]}",)
    return ("strike", "land", "channel")


def _mind_bloom(ctx: EventContext) -> tuple[str, ...]:
    if ctx.ratio >= 0.45:
        return ("i am war", "i am rich", "i am awake")
    return ("i am rich", "i am war", "i am awake")


def _moai(ctx: EventContext) -> tuple[str, ...]:
    if "golden idol" in ctx.relics:
        return ("jump inside", "pry it open", "leave") if ctx.ratio < 0.5 else ("pry it open", "leave")
    return ("leave", "jump inside")


def _sphere(ctx: EventContext) -> tuple[str, ...]:
    return ("open sphere", "leave") if ctx.ratio >= 0.7 else ("leave", "open sphere")


def _secret_portal(ctx: EventContext) -> tuple[str, ...]:
    return ("leave", "enter")


def _sensory_stone(ctx: EventContext) -> tuple[str, ...]:
    return ("__index:0",)


def _tomb(ctx: EventContext) -> tuple[str, ...]:
    return ("offer gold", "leave") if "red mask" in ctx.relics else ("leave", "offer gold")


def _winding_halls(ctx: EventContext) -> tuple[str, ...]:
    return ("embrace madness", "retreat", "focus") if ctx.ratio >= 0.5 else ("retreat", "embrace madness", "focus")


def _single(ctx: EventContext) -> tuple[str, ...]:
    return ("__index:0",)


RULES: dict[str, Callable[[EventContext], tuple[str, ...]]] = {
    "neow": _neow,
    "big_fish": _big_fish,
    "the_cleric": _cleric,
    "dead_adventurer": _dead_adventurer,
    "golden_idol": _golden_idol,
    "golden_wing": _golden_wing,
    "world_of_goop": _goop,
    "living_wall": _living_wall,
    "mushrooms": _mushrooms,
    "scrap_ooze": _scrap_ooze,
    "shining_light": _shining_light,
    "liars_game": _liars_game,
    "accursed_blacksmith": _blacksmith,
    "bonfire": _bonfire,
    "wheel": _single,
    "purifier": _purifier,
    "transmogrifier": _transmogrifier,
    "upgrade_shrine": _upgrade_shrine,
    "duplicator": _duplicator,
    "golden_shrine": _golden_shrine,
    "lab": _lab,
    "match_and_keep": _match_and_keep,
    "note_for_yourself": _note,
    "we_meet_again": _we_meet_again,
    "fountain": _fountain,
    "designer": _designer,
    "face_trader": _face_trader,
    "woman_in_blue": _woman_in_blue,
    "addict": _addict,
    "back_to_basics": _back_to_basics,
    "beggar": _beggar,
    "colosseum": _colosseum,
    "cursed_tome": _cursed_tome,
    "drug_dealer": _drug_dealer,
    "forgotten_altar": _forgotten_altar,
    "ghosts": _ghosts,
    "knowing_skull": _knowing_skull,
    "masked_bandits": _masked_bandits,
    "nest": _nest,
    "nloth": _nloth,
    "the_joust": _joust,
    "the_library": _library,
    "the_mausoleum": _mausoleum,
    "vampires": _vampires,
    "falling": _falling,
    "mind_bloom": _mind_bloom,
    "moai_head": _moai,
    "mysterious_sphere": _sphere,
    "secret_portal": _secret_portal,
    "sensory_stone": _sensory_stone,
    "tomb_of_lord_red_mask": _tomb,
    "winding_halls": _winding_halls,
    "spire_heart": _single,
}

DEFAULT_PREFERENCE = ("leave", "__safest")
_RISKY = ("lose", "curse", "fight", "hp", "damage", "max hp", "starting relic", "all gold")


def rank_choices(
    key: str | None, ctx: EventContext, legal: Sequence[int]
) -> tuple[list[int], str]:
    """Return legal choice ids in preference order and the rule used."""

    rule = RULES.get(key or "")
    markers = rule(ctx) if rule is not None else DEFAULT_PREFERENCE
    ordered: list[int] = []
    for marker in markers:
        if marker == "__score:neow":
            scored = sorted(legal, key=lambda i: (-neow_option_score(ctx.texts[i], ctx), i))
            ordered.extend(i for i in scored if i not in ordered)
            continue
        if marker == "__safest":
            safest = sorted(
                legal,
                key=lambda i: (sum(word in ctx.texts[i] for word in _RISKY), i),
            )
            ordered.extend(i for i in safest if i not in ordered)
            continue
        if marker.startswith("__index:"):
            index = int(marker.split(":", 1)[1])
            if index in legal and index not in ordered:
                ordered.append(index)
            continue
        for index in legal:
            if index not in ordered and marker in ctx.labels[index]:
                ordered.append(index)
    ordered.extend(i for i in legal if i not in ordered)
    return ordered, (key or "default")


__all__ = ["DEFAULT_PREFERENCE", "EventContext", "RULES", "event_key", "normalize", "rank_choices"]
