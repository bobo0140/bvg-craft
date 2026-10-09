"""
events.py — превръща редовете от конзолата в събития.

Това са очите на AI-то: чат, влизане, излизане, смърт с причината,
постижения и IP адресът при влизане. Всичко се вижда в конзолата, без
мод и без плъгин.

Редовете първо се чистят от ANSI цветове: Paper ги добавя на системните
съобщения (жълтото на „joined the game"), а с тях редът не съвпада с нито
един шаблон и влизането просто се губи.
"""

import re

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")
CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
SECTION = re.compile("\u00a7.")

PREFIX = re.compile(r"^\[[^\]]*\]\s*(?:\[[^\]]*\]\s*)?:?\s*")

CHAT = re.compile(r"^(?:\[Not Secure\]\s*)?<([^>]{1,32})>\s(.+)$")
JOIN = re.compile(r"^([A-Za-z0-9_]{1,16}) joined the game$")
LEAVE = re.compile(r"^([A-Za-z0-9_]{1,16}) left the game$")
# Ivan[/192.168.1.5:51234] logged in with entity id 87 at (...)
LOGIN = re.compile(r"^([A-Za-z0-9_]{1,16})\[/(.+?):(\d+)\] logged in with "
                   r"entity id")
ADV = re.compile(r"^([A-Za-z0-9_]{1,16}) has (?:made the advancement|"
                 r"completed the challenge|reached the goal) \[(.+)\]$")
READY = re.compile(r'Done \([\d.,]+s\)! For help, type "help"')
LIST = re.compile(r"There are (\d+) of a max of (\d+) players online:\s*(.*)$",
                  re.S)

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


def clean(line: str) -> str:
    """Маха цветовете и управляващите знаци, оставя чист текст."""
    line = ANSI.sub("", line)
    line = SECTION.sub("", line)
    return CTRL.sub("", line).strip()


def decode(raw: bytes) -> str:
    """Декодира ред от конзолата.

    Java на български Windows пише в cp1251, ако не е казано другояче, а
    четена като UTF-8 кирилицата става шум и духовете не разпознават
    името си. Затова първо опитваме UTF-8 и при грешка — cp1251.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1251")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")


def strip(line: str) -> str:
    return PREFIX.sub("", clean(line), count=1).strip()


def parse_list(reply: str):
    """Отговорът на `list` -> (брой, максимум, [имена]) или None."""
    m = LIST.search(SECTION.sub("", reply or ""))
    if not m:
        return None
    names = [n.strip() for n in m.group(3).split(",") if n.strip()]
    return int(m.group(1)), int(m.group(2)), names


def parse(line: str, online=None):
    """Връща събитие като речник или None."""
    raw = clean(line)
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
    m = LOGIN.match(text)
    if m:
        # IPv6 идва в скоби: Name[/[0:0:0:0:0:0:0:1]:5555]
        return {"type": "login", "player": m.group(1),
                "ip": m.group(2).strip("[]"),
                "port": int(m.group(3))}
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
