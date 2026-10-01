"""
events.py — превръща редовете от конзолата в събития.

Това са очите на AI-то: чат, влизане, излизане, смърт с причината и
постижения. Всичко се вижда в конзолата, без мод и без плъгин.
"""

import re

# „[12:00:00 INFO]: " в началото на реда
PREFIX = re.compile(r"^\[[^\]]*\]\s*(?:\[[^\]]*\]\s*)?:?\s*")

# В офлайн режим чатът идва с [Not Secure] отпред
CHAT = re.compile(r"^(?:\[Not Secure\]\s*)?<([^>]{1,32})>\s(.+)$")
JOIN = re.compile(r"^([A-Za-z0-9_]{1,16}) joined the game$")
LEAVE = re.compile(r"^([A-Za-z0-9_]{1,16}) left the game$")
ADV = re.compile(r"^([A-Za-z0-9_]{1,16}) has (?:made the advancement|"
                 r"completed the challenge|reached the goal) \[(.+)\]$")
READY = re.compile(r'Done \([\d.,]+s\)! For help, type "help"')

# Глаголи от съобщенията за смърт на Minecraft
DEATH_WORDS = (
    "was slain", "was shot", "was blown up", "was killed", "was fireballed",
    "was pummeled", "was squashed", "was impaled", "was stung",
    "was pricked", "was poked", "was skewered", "was struck by lightning",
    "was frozen", "was burned", "was roasted", "was obliterated",
    "was squished", "was doomed", "was speared", "was smashed",
    "fell ", "fell off", "hit the ground", "drowned", "burned to death",
    "went up in flames", "walked into fire", "tried to swim in lava",
    "suffocated", "starved", "died", "blew up", "withered away",
    "experienced kinetic energy", "froze to death", "discovered the floor",
    "went off with a bang", "didn't want to live", "left the confines",
)


def strip(line: str) -> str:
    return PREFIX.sub("", line.strip(), count=1).strip()


def parse(line: str, online=None):
    """Връща събитие като речник или None.

    online — известните играчи; помага да не сбъркаме обикновен ред от
    конзолата за смърт.
    """
    raw = line.strip()
    if not raw:
        return None
    if READY.search(raw):
        return {"type": "ready"}

    text = strip(raw)

    m = CHAT.match(text)
    if m:
        return {"type": "chat", "player": m.group(1), "message": m.group(2)}

    m = JOIN.match(text)
    if m:
        return {"type": "join", "player": m.group(1)}

    m = LEAVE.match(text)
    if m:
        return {"type": "leave", "player": m.group(1)}

    m = ADV.match(text)
    if m:
        return {"type": "advancement", "player": m.group(1),
                "name": m.group(2)}

    first, _, rest = text.partition(" ")
    if rest and any(rest.startswith(w) or w in rest for w in DEATH_WORDS):
        if online is None or first in online:
            killer = None
            km = re.search(r" by ([^\s].*?)(?: using .*)?$", rest)
            if km:
                killer = km.group(1)
            return {"type": "death", "player": first, "message": text,
                    "cause": rest, "killer": killer}
    return None
