"""
safety.py — какво AI-то има право да прави.

Модел с достъп до конзолата може с една дума да изтрие света, да свали
сървъра или да даде на някого права на админ. Затова всяка команда
минава оттук. Героите имат различни права; режисьорът има почти всичко,
но никога неща, които чупят сървъра или трият чужд труд.
"""

import re

FILL_LIMIT = 32768
MAX_PER_REPLY = 80

# Никога, за никого. Като цели думи — иначе „ban" спира и „banner".
FORBIDDEN = re.compile(
    r"\b(?:stop|op|deop|whitelist|ban|ban-ip|pardon|pardon-ip|kick|"
    r"save-off|save-all|save-on|reload|debug|datapack|function|forceload|"
    r"worldborder|publish|perf|jfr|transfer|setidletimeout|difficulty|"
    r"defaultgamemode|setworldspawn|tick|random|return|test|version|"
    r"gamerule|fetchprofile|dialog|stopwatch)\b"
    r"|kill\s+@[aprs]|type=player|type=minecraft:player",
    re.I)

# Права по групи
POWERS = {
    "build": {"setblock", "fill", "clone", "place"},
    "effects": {"particle", "playsound", "stopsound", "title", "tellraw",
                "effect"},
    "mobs": {"summon"},
    "items": {"give", "clear", "item", "loot"},
    "world": {"time", "weather"},
    "move": {"tp", "teleport", "spreadplayers"},
    "score": {"scoreboard", "bossbar", "tag", "attribute", "data", "team"},
    # само за режисьора с пълен достъп
    "players": {"experience", "xp", "gamemode", "spawnpoint", "damage",
                "ride", "enchant", "kill", "attribute", "item", "loot",
                "clear", "say", "msg", "tell", "w", "summon"},
}

FULL = list(POWERS)

# Шегаджията може да вика само безобидни създания
SAFE_MOBS = {
    "chicken", "cow", "pig", "sheep", "rabbit", "cat", "parrot", "fox",
    "frog", "goat", "llama", "bee", "axolotl", "allay", "armadillo",
    "firework_rocket", "item", "snow_golem", "villager", "squid",
    "dolphin", "turtle", "camel", "sniffer", "panda", "polar_bear",
    "glow_squid", "bat", "slime", "lightning_bolt", "text_display",
    "marker", "armor_stand", "falling_block", "experience_orb",
    "happy_ghast", "copper_golem",
}

# Никой не ги вика: рушат постройки или свалят сървъра
NEVER_MOBS = {
    "wither", "ender_dragon", "tnt", "tnt_minecart", "end_crystal",
    "fireball", "small_fireball", "dragon_fireball", "wither_skull",
    "creeper", "command_block_minecart", "giant", "ghast",
}

NEVER_ITEMS = {
    "command_block", "chain_command_block", "repeating_command_block",
    "command_block_minecart", "structure_block", "jigsaw", "debug_stick",
    "bedrock", "barrier", "structure_void", "spawner", "trial_spawner",
    "test_block", "test_instance_block", "light",
}

# Ефекти, които не убиват и не пречат за дълго
SAFE_EFFECTS = {
    "speed", "slowness", "jump_boost", "levitation", "slow_falling",
    "glowing", "night_vision", "invisibility", "nausea", "blindness",
    "darkness", "haste", "mining_fatigue", "luck", "dolphins_grace",
    "regeneration", "absorption", "resistance", "fire_resistance",
    "water_breathing", "saturation", "hero_of_the_village", "strength",
    "health_boost", "conduit_power", "weaving", "oozing", "infested",
    "wind_charged",
}
HARSH_EFFECTS = {"poison", "wither", "instant_damage", "hunger", "weakness",
                 "bad_omen", "unluck", "instant_health", "raid_omen",
                 "trial_omen"}

# Кого може да убие режисьорът: чудовища и неговите собствени неща
KILLABLE = {
    "zombie", "skeleton", "spider", "cave_spider", "husk", "stray",
    "drowned", "phantom", "witch", "pillager", "vindicator", "evoker",
    "ravager", "slime", "magma_cube", "blaze", "zombie_villager",
    "silverfish", "endermite", "vex", "bogged", "breeze", "warden",
    "hoglin", "zoglin", "piglin_brute", "guardian", "item", "arrow",
    "experience_orb", "chicken", "falling_block", "text_display",
    "marker", "armor_stand", "lightning_bolt", "creaking",
}
PROTECTED_TAGS = ("bvg_npc", "bvg_worker", "bvg_keeper", "bvg_builder",
                  "bvg_trickster", "bvg_trader", "bvg_w")


def _head(cmd: str) -> str:
    """Командата отпред, като се мине през execute ... run ..."""
    low = cmd.lower().strip()
    if low.startswith("execute"):
        m = re.search(r"\brun\s+(\S+)", low)
        return m.group(1) if m else "execute"
    return low.split()[0] if low else ""


def _inner(cmd: str) -> str:
    low = cmd.strip()
    if low.lower().startswith("execute"):
        m = re.search(r"\brun\s+(.+)$", low, re.I)
        return m.group(1) if m else ""
    return low


def _volume(n):
    return ((abs(n[3] - n[0]) + 1) * (abs(n[4] - n[1]) + 1)
            * (abs(n[5] - n[2]) + 1))


def _coords(parts, start=1, count=6):
    try:
        return [int(float(x)) for x in parts[start:start + count]]
    except (ValueError, IndexError):
        return None


def _split_fill(cmd):
    parts = cmd.split()
    n = _coords(parts)
    if n is None:
        # относителни координати (~) — пропускаме, ако не е огромно
        return [cmd] if all("~" in p or "^" in p for p in parts[1:7]) \
            else None
    if _volume(n) <= FILL_LIMIT:
        return [cmd]
    rest = " ".join(parts[7:])
    x1, x2 = sorted((n[0], n[3]))
    y1, y2 = sorted((n[1], n[4]))
    z1, z2 = sorted((n[2], n[5]))
    step = max(1, int(max(1, FILL_LIMIT // (z2 - z1 + 1)) ** 0.5))
    out = []
    for xs in range(x1, x2 + 1, step):
        for ys in range(y1, y2 + 1, step):
            out.append(f"fill {xs} {ys} {z1} {min(x2, xs + step - 1)} "
                       f"{min(y2, ys + step - 1)} {z2} {rest}")
    return out


def _reason(cmd, head, powers):
    """Защо командата не се допуска, или None."""
    low = cmd.lower()
    inner = _inner(cmd)
    ilow = inner.lower()
    full = "players" in powers

    if head == "summon":
        mob = ilow.split("summon", 1)[1].split()[0] if " " in ilow else ""
        mob = mob.replace("minecraft:", "")
        if mob in NEVER_MOBS:
            return f"опасно създание ({mob})"
        if not full and "build" not in powers and mob not in SAFE_MOBS:
            return f"опасно създание ({mob})"
    elif head == "effect" and " give " in f" {ilow} ":
        m = re.search(r"effect\s+give\s+\S+\s+(?:minecraft:)?(\w+)"
                      r"(?:\s+(\d+|infinite))?(?:\s+(\d+))?", ilow)
        if m:
            eff, secs, amp = m.group(1), m.group(2), m.group(3)
            if secs == "infinite":
                return "безкраен ефект"
            secs = int(secs or 30)
            amp = int(amp or 0)
            if eff in HARSH_EFFECTS:
                if not full:
                    return f"опасен ефект ({eff})"
                if amp > 1 or secs > 30:
                    return f"твърде силен ефект ({eff})"
            elif eff not in SAFE_EFFECTS and not full:
                return f"опасен ефект ({eff})"
            if secs > (300 if full else 120):
                return "твърде дълъг ефект"
            if amp > 5:
                return "твърде силен ефект"
    elif head == "clear":
        parts = ilow.split()
        if "items" not in powers and not full:
            return "без право да взима предмети"
        if len(parts) < 3:
            return "не може да изпразни целия инвентар"
    elif head == "give":
        m = re.search(r"give\s+\S+\s+(?:minecraft:)?([a-z0-9_]+)"
                      r"(?:\[[^\]]*\])?(?:\s+(\d+))?", ilow)
        if m:
            if m.group(1) in NEVER_ITEMS:
                return f"забранен предмет ({m.group(1)})"
            if m.group(2) and int(m.group(2)) > 64:
                return "твърде много наведнъж"
    elif head == "gamemode":
        if not re.search(r"\bgamemode\s+(survival|adventure)\b", ilow):
            return "само оцеляване или приключение"
    elif head == "kill":
        if any(t in ilow for t in PROTECTED_TAGS):
            return "не убива героите и селяните"
        m = re.search(r"type=!?(?:minecraft:)?([a-z_]+)", ilow)
        tag = re.search(r"tag=bvg_[a-z0-9_]+", ilow)
        if not ilow.strip().startswith("kill @e[") or \
                not ((m and m.group(1) in KILLABLE and "type=!" not in ilow)
                     or tag):
            return "убива само чудовища и свои неща"
    elif head == "fill":
        n = _coords(inner.split())
        target = (inner.split()[7] if len(inner.split()) > 7 else "").lower()
        if n and target.replace("minecraft:", "") in ("air", "cave_air",
                                                      "void_air") \
                and _volume(n) > 2000 and "replace" not in ilow:
            return "твърде голямо изтриване"
        if n and _volume(n) > 200000:
            return "твърде голяма зона"
    elif head == "clone":
        n = _coords(inner.split(), 1, 6)
        if n and _volume(n) > 8192:
            return "твърде голямо копиране"
    elif head == "data":
        if re.search(r"data\s+(?:modify|merge|remove)\s+entity\s+[A-Za-z0-9_]{1,16}\b",
                     inner):
            return "не пипа данните на играчите"
    elif head == "attribute":
        m = re.search(r"base\s+set\s+(-?[\d.]+)", ilow)
        if m and "scale" in ilow and not (0.2 <= float(m.group(1)) <= 4):
            return "размерът е извън разумното"
    elif head in ("tp", "teleport"):
        m = re.search(r"\s(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+"
                      r"(-?\d+(?:\.\d+)?)", inner)
        if m and not (-60 <= float(m.group(2)) <= 330):
            return "телепорт в празнотата"
    if "@e" in low and head in ("tp", "teleport") and "limit=" not in low \
            and "type=" not in low and "tag=" not in low:
        return "твърде широк избор (@e)"
    return None


def check(commands, powers, log=None, limit=MAX_PER_REPLY):
    """Пропуска само позволеното. Връща (позволени, отказани)."""
    allowed_heads = set()
    for p in powers:
        allowed_heads |= POWERS.get(p, set())

    ok, refused = [], []
    for raw in commands or []:
        if not isinstance(raw, str):
            continue
        cmd = raw.strip().lstrip("/").strip()
        if not cmd or len(cmd) > 30000:
            continue
        head = _head(cmd)

        reason = None
        # Текстът в кавички не е команда: „стоп" в репликата не е stop
        bare = re.sub(r'"(?:[^"\\]|\\.)*"', '""', cmd)
        if FORBIDDEN.search(bare):
            reason = "забранена"
        elif head not in allowed_heads:
            reason = f"няма право на {head}"
        else:
            reason = _reason(cmd, head, powers)

        if reason:
            refused.append((cmd, reason))
            continue

        if head == "fill" and not cmd.lower().startswith("execute"):
            parts = _split_fill(cmd)
            if parts is None:
                refused.append((cmd, "лоши координати"))
                continue
            ok.extend(parts)
        else:
            ok.append(cmd)
        if len(ok) >= limit:
            break

    if log:
        for cmd, why in refused[:6]:
            log(f"Отказах: {cmd[:60]} — {why}")
    return ok[:limit], refused
