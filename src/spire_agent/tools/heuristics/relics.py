"""Fixed relic preference tables for Boss rewards and shops."""

from __future__ import annotations

import re


# Boss relic scores; higher is better.  Runic Dome hides intents, which the
# MCTS combat agent depends on, so it is effectively never taken.
_BOSS_RELICS = {
    "black blood": 8.2,
    "frozen core": 7.8,
    "nuclear battery": 7.8,
    "coffee dripper": 7.6,
    "empty cage": 7.6,
    "astrolabe": 7.2,
    # Runic Pyramid retains the Heart's Wounds/Dazed forever (run 10: Reaper+
    # sat behind three Wounds for four turns); Philosopher's Stone adds +1 per
    # hit to the Heart's 12-hit attack.  Both are fine in Acts 1-3, but the
    # goal is the Heart.
    "runic pyramid": 4.9,
    "slaver's collar": 6.8,
    "philosopher's stone": 4.7,
    "inserter": 6.2,
    "sacred bark": 5.6,
    "pandora's box": 5.2,
    "black star": 5.0,
    "hovering kite": 5.0,
    "wrist blade": 5.0,
    "ring of the serpent": 5.0,
    "holy water": 5.0,
    "violet lotus": 5.0,
    # The bot opens every chest and takes most rewards, so Cursed Key fills the
    # deck with curses that removals then have to spend themselves on.
    "cursed key": 4.8,
    "busted crown": 4.6,
    # The combat agent uses potions; Sozu throws that away.
    "sozu": 4.4,
    "mark of pain": 4.0,
    "snecko eye": 3.8,
    "velvet choker": 3.6,
    "runic cube": 3.6,
    "ectoplasm": 3.2,
    "calling bell": 3.0,
    "tiny house": 3.0,
    # Smithing is the bot's only reliable upgrade path (2026-09-05: the two
    # Fusion Hammer runs reached the Heart with 4-5 upgrades and died).
    "fusion hammer": 1.5,
    "runic dome": 0.0,
}

# Shop relics that open selectors or otherwise need a plan; never buy them.
_SHOP_AVOID = frozenset(
    {
        "dolly's mirror", "bottled flame", "bottled lightning", "bottled tornado",
        "orrery", "prismatic shard", "cauldron", "juzu bracelet", "ectoplasm",
    }
)

_SHOP_RELICS = {
    "membership card": 2.4, "bag of preparation": 2.2, "anchor": 2.0,
    "vajra": 2.2, "orichalcum": 2.0, "horn cleat": 1.8, "kunai": 2.0,
    "shuriken": 2.0, "ornamental fan": 1.9, "pen nib": 1.9, "gremlin horn": 1.9,
    "meat on the bone": 2.0, "mercury hourglass": 1.9, "ice cream": 2.3,
    "incense burner": 2.1, "dead branch": 1.8, "tungsten rod": 2.2,
    "bird-faced urn": 1.9, "charon's ashes": 1.8, "fossilized helix": 2.1,
    "girya": 1.9, "lizard tail": 2.0, "mango": 1.9, "pear": 1.8, "strawberry": 1.7,
    "toy ornithopter": 1.5, "lantern": 1.8, "bag of marbles": 1.8, "blood vial": 1.7,
    "bronze scales": 1.6, "centennial puzzle": 1.5, "oddly smooth stone": 1.6,
    "happy flower": 1.7, "akabeko": 1.5, "red skull": 1.5, "self-forming clay": 1.9,
    "paper phrog": 1.9, "letter opener": 1.6, "molten egg": 1.5, "toxic egg": 1.6,
    "frozen egg": 1.4, "eternal feather": 1.8, "meal ticket": 1.7,
    "pantograph": 1.7, "captain's wheel": 1.7, "stone calendar": 1.6,
    "thread and needle": 1.8, "torii": 1.9, "tough bandages": 1.3, "unceasing top": 1.7,
    "calipers": 1.9, "champion belt": 1.6, "du-vu doll": 1.4, "ginger": 1.8,
    "turnip": 1.7, "the boot": 1.5, "shovel": 1.2, "singing bowl": 1.3,
    "strike dummy": 1.3, "art of war": 1.6, "regal pillow": 1.6, "nunchaku": 1.6,
    "sling of courage": 1.7, "hand drill": 1.4, "lee's waffle": 1.9, "medical kit": 1.6,
    "toolbox": 1.4, "brimstone": 1.4, "clockwork souvenir": 1.7, "frozen eye": 1.3,
    "chemical x": 1.5, "runic capacitor": 1.6, "data disk": 1.6, "gold-plated cables": 1.6,
    "symbiotic virus": 1.7, "emotion chip": 1.5, "magic flower": 1.6, "peace pipe": 1.8,
}


# Shop potions: purchase score before the act bonus.  Anything unlisted is 1.1.
_SHOP_POTIONS = {
    "fairy in a bottle": 1.9, "fruit juice": 1.9, "entropic brew": 1.6,
    "heart of iron": 1.6, "ancient potion": 1.5, "duplication potion": 1.5,
    "cultist potion": 1.5, "strength potion": 1.5, "power potion": 1.5,
    "ghost in a jar": 1.5, "focus potion": 1.5, "block potion": 1.4,
    "fear potion": 1.4, "speed potion": 1.4, "regen potion": 1.4,
    "attack potion": 1.4, "blessing of the forge": 1.4, "essence of steel": 1.4,
    "blood potion": 1.4, "energy potion": 1.4, "weak potion": 1.3,
    "swift potion": 1.3, "fire potion": 1.3, "skill potion": 1.3,
    "liquid bronze": 1.3, "dexterity potion": 1.3, "flex potion": 1.2,
    "explosive potion": 1.2, "distilled chaos": 1.2, "gambler's brew": 1.2,
    "liquid memories": 1.2, "colorless potion": 1.2, "elixir": 1.0,
    "snecko oil": 1.0, "smoke bomb": 0.9, "poison potion": 1.3,
    "cunning potion": 1.2, "bottled miracle": 1.2, "stance potion": 1.0,
    "ambrosia": 1.2, "potion of capacity": 1.3, "essence of darkness": 1.3,
}


def normalize(name: object) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().casefold())


def boss_relic_value(name: object) -> float:
    return _BOSS_RELICS.get(normalize(name), 4.5)


def shop_relic_value(name: object, price: float) -> float | None:
    """Return a purchase score, or None when the relic must not be bought."""

    key = normalize(name)
    if key in _SHOP_AVOID:
        return None
    if key in _SHOP_RELICS:
        return _SHOP_RELICS[key]
    # Unknown relic: price is a weak proxy for rarity.
    return 1.0 + min(max(price, 0.0), 300.0) / 300.0 * 0.5


def shop_potion_value(name: object, act: object = 1) -> float:
    """Purchase score for a shop potion; Acts 3-4 pay extra for the Heart."""

    value = _SHOP_POTIONS.get(normalize(name), 1.1)
    try:
        late = int(act) >= 3
    except (TypeError, ValueError):
        late = False
    return value + (0.4 if late else 0.0)


__all__ = ["boss_relic_value", "shop_potion_value", "shop_relic_value"]
