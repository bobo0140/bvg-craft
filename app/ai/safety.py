"""
safety.py — какво AI-то има право да прави.

Модел с достъп до конзолата може с една дума да изтрие света, да свали
сървъра или да даде на някого права на админ. Затова всяка команда
минава оттук, а различните герои имат различни права: шегаджията не
може да строи, търговката не може да пуска ефекти.
"""

import re

FILL_LIMIT = 32768
MAX_PER_REPLY = 80

# Никога, за никого. Като цели думи — иначе „ban" спира и „banner".
FORBIDDEN = re.compile(
    r"\b(?:stop|op|deop|whitelist|ban|ban-ip|pardon|pardon-ip|kick|"
    r"save-off|save-all|reload|debug|datapack|function|forceload|"
    r"worldborder|publish|perf|jfr|transfer|setidletimeout|difficulty|"
    r"defaultgamemode)\b"
    r"|kill\s+@[aprs]|type=player|gamerule\s+(?:keepinventory|dofiretick)",
    re.I)

# Права по групи
POWERS = {
    "build": {"setblock", "fill", "clone", "place"},
    "effects": {"particle", "playsound", "title", "tellraw", "effect"},
    "mobs": {"summon"},
    "items": {"give", "clear", "item"},
    "world": {"time", "weather"},
    "move": {"tp", "teleport", "spreadplayers"},
    "score": {"scoreboard", "bossbar", "tag", "attribute", "data"},
}

# Шегаджията може да вика само безобидни създания
SAFE_MOBS = {
    "chicken", "cow", "pig", "sheep", "rabbit", "cat", "parrot", "fox",
    "frog", "goat", "llama", "bee", "axolotl", "allay", "armadillo",
    "firework_rocket", "item", "snow_golem", "villager", "squid",
    "dolphin", "turtle", "camel", "sniffer", "panda", "polar_bear",
    "glow_squid", "bat", "slime", "lightning_bolt",
}

# Ефекти, които не убиват и не пречат за дълго
SAFE_EFFECTS = {
    "speed", "slowness", "jump_boost", "levitation", "slow_falling",
    "glowing", "night_vision", "invisibility", "nausea", "blindness",
    "darkness", "haste", "mining_fatigue", "luck", "dolphins_grace",
    "regeneration", "absorption", "resistance", "fire_resistance",
    "water_breathing", "saturation", "hero_of_the_village",
}


def _head(cmd: str) -> str:
    """Командата отпред, като се мине през execute ... run ..."""
    low = cmd.lower().strip()
    if low.startswith("execute"):
        m = re.search(r"\brun\s+(\S+)", low)
        return m.group(1) if m else "execute"
    return low.split()[0] if low else ""


def _split_fill(cmd):
    parts = cmd.split()
    try:
        n = [int(float(x)) for x in parts[1:7]]
    except (ValueError, IndexError):
        return None
    vol = ((abs(n[3] - n[0]) + 1) * (abs(n[4] - n[1]) + 1)
           * (abs(n[5] - n[2]) + 1))
    if vol <= FILL_LIMIT:
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
        if not cmd:
            continue
        low = " " + cmd.lower() + " "
        head = _head(cmd)

        reason = None
        if FORBIDDEN.search(cmd):
            reason = "забранена"
        elif head not in allowed_heads:
            reason = f"този герой няма право на {head}"
        elif head == "summon" and "build" not in powers:
            mob = cmd.lower().split("summon", 1)[1].split()[0]
            mob = mob.replace("minecraft:", "")
            if mob not in SAFE_MOBS:
                reason = f"опасно създание ({mob})"
        elif head == "effect" and "give" in cmd.lower():
            m = re.search(r"effect\s+give\s+\S+\s+(?:minecraft:)?(\w+)",
                          cmd.lower())
            if m and m.group(1) not in SAFE_EFFECTS:
                reason = f"опасен ефект ({m.group(1)})"
            m2 = re.search(r"effect\s+give\s+\S+\s+\S+\s+(\d+)", cmd.lower())
            if not reason and m2 and int(m2.group(1)) > 120:
                reason = "твърде дълъг ефект"
        elif head == "clear" and "items" not in powers:
            reason = "без право да взима предмети"

        if reason:
            refused.append((cmd, reason))
            continue

        if head == "fill":
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
