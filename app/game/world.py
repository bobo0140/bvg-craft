"""
world.py — помощници за действия в света.

Всичко, което се изпраща към играта, минава оттук, за да има едно
място, където се грижим за кавичките и формата на имената. Командите
са във формата от 1.21.5 нататък (текстовете и NBT като SNBT).
"""

import itertools
import json
import re
import threading

from ..ai.characters import CHARACTERS

COLORS = {"black", "dark_blue", "dark_green", "dark_aqua", "dark_red",
          "dark_purple", "gold", "gray", "dark_gray", "blue", "green",
          "aqua", "red", "light_purple", "yellow", "white"}

_probe_ids = itertools.count(1)
_probe_lock = threading.Lock()


def esc(text) -> str:
    """Безопасен текст в низ на Minecraft (кавички, нови редове)."""
    text = re.sub(r"[\r\n\t]+", " ", str(text))
    return json.dumps(text, ensure_ascii=False)[1:-1]


def color(c, default="white"):
    c = str(c or "").lower().strip()
    return c if c in COLORS else default


def name_ok(name) -> bool:
    """Истинско име на играч — не селектор и не нещо странно."""
    return bool(re.fullmatch(r"[A-Za-z0-9_]{1,16}", str(name or "")))


def target_ok(t) -> bool:
    return name_ok(t) or t in ("@a", "@p", "@r")


# ---------- говорене ----------

def say(sender, char_key, text):
    """Реплика на герой в чата, с цветно име."""
    ch = CHARACTERS[char_key]
    speak(sender, ch["name"], ch["color"], text)


def speak(sender, name, col, text, target="@a"):
    sender.send(
        f'tellraw {target} [{{"text":"[{esc(name)}] ","color":"{color(col)}",'
        f'"bold":true}},{{"text":"{esc(text)}","color":"white"}}]')


def speak_near(sender, name, col, text, pos, radius=48):
    """Само играчите наблизо чуват — селяните не крещят на целия свят."""
    x, y, z = pos
    sender.send(
        f"execute positioned {x:.1f} {y:.1f} {z:.1f} run tellraw "
        f'@a[distance=..{radius}] [{{"text":"[{esc(name)}] ","color":'
        f'"{color(col)}"}},{{"text":"{esc(text)}","color":"white"}}]')


def whisper(sender, player, char_key, text):
    ch = CHARACTERS[char_key]
    sender.send(
        f'tellraw {player} [{{"text":"[{esc(ch["name"])}] ","color":'
        f'"{ch["color"]}"}},{{"text":"{esc(text)}","color":"gray",'
        f'"italic":true}}]')


def tell(sender, target, text, col="gray"):
    """Системно съобщение (без име отпред)."""
    sender.send(f'tellraw {target} {{"text":"{esc(text)}",'
                f'"color":"{color(col, "gray")}"}}')


def announce(sender, title, subtitle="", col="gold", target="@a"):
    sender.send(f"title {target} times 10 70 20")
    sender.send(f'title {target} title {{"text":"{esc(title)}",'
                f'"color":"{color(col, "gold")}","bold":true}}')
    if subtitle:
        sender.send(f'title {target} subtitle {{"text":"{esc(subtitle)}",'
                    f'"color":"gray"}}')


def actionbar(sender, target, text, col="yellow"):
    sender.send(f'title {target} actionbar {{"text":"{esc(text)}",'
                f'"color":"{color(col, "yellow")}"}}')


def sound(sender, target, name, pitch=1.0, volume=1.0):
    sender.send(f"execute at {target} run playsound {name} master "
                f"{target} ~ ~ ~ {volume} {pitch}", optional=True)


def firework(sender, target, colors=(16711680, 16766720, 65535)):
    cols = ",".join(str(int(c)) for c in colors)
    sender.send(
        f"execute at {target} run summon firework_rocket ~ ~1 ~ "
        f"{{LifeTime:25,FireworksItem:{{id:\"minecraft:firework_rocket\","
        f"count:1,components:{{\"minecraft:fireworks\":{{flight_duration:1,"
        f"explosions:[{{shape:\"large_ball\",colors:[I;{cols}],"
        f"has_twinkle:true,has_trail:true}}]}}}}}}}}", optional=True)


ITEM_RE = re.compile(r"^(?:minecraft:)?([a-z0-9_]{2,40})(?:\s+(\d{1,4}))?$")


def item_spec(spec):
    """'diamond 3' -> ('diamond', 3) или None за нещо неразбираемо."""
    m = ITEM_RE.match(str(spec or "").strip().lower())
    if not m:
        return None
    return m.group(1), max(1, min(int(m.group(2) or 1), 64))


def reward(sender, player, items=(), xp=0, title=None, reason=""):
    """Награда: предмети, опит, надпис и фойерверк."""
    given = []
    for spec in items or []:
        it = item_spec(spec)
        if it:
            sender.send(f"give {player} minecraft:{it[0]} {it[1]}")
            given.append(f"{it[1]}× {it[0]}")
    if xp:
        sender.send(f"experience add {player} {int(xp)} points")
    if title:
        announce(sender, title, reason, "gold", target=player)
    firework(sender, player)
    sound(sender, player, "minecraft:ui.toast.challenge_complete")
    return given


# ---------- балончета и духове ----------

def bubble(sender, char_key, text):
    """Балонче с репликата над главата на героя."""
    bubble_tag(sender, f"bvg_{char_key}", text)


def bubble_tag(sender, tag, text, height=2.4, col="white"):
    short = esc(str(text)[:90])
    sender.send(f"kill @e[tag={tag}_bubble]")
    sender.send(
        f"execute at @e[tag={tag},limit=1] run summon text_display ~ "
        f"~{height} ~ "
        f'{{text:{{text:"{short}",color:"{color(col)}"}},billboard:"center",'
        f'Tags:["{tag}_bubble","bvg_bubble"],background:1073741824,'
        f"view_range:3.0f,line_width:180}}", optional=True)


def spawn_npc(sender, char_key, pos, facing_yaw=0.0):
    """Героят като селянин, който не мърда и не може да бъде убит."""
    ch = CHARACTERS[char_key]
    spawn_villager(sender, f"bvg_{char_key}", ch["name"], ch["color"],
                   ch["profession"], pos, facing_yaw, extra_tags=("bvg_npc",))


def recipes(offers):
    """[("emerald 2", "diamond 1"), ...] -> Recipes за истинска търговия."""
    parts = []
    for buy, sell in offers or []:
        b, s = item_spec(buy), item_spec(sell)
        if b and s:
            parts.append(f'{{buy:{{id:"minecraft:{b[0]}",count:{b[1]}}},'
                         f'sell:{{id:"minecraft:{s[0]}",count:{s[1]}}},'
                         f"maxUses:8,rewardExp:0b}}")
    return ",".join(parts)


def spawn_villager(sender, tag, name, col, profession, pos, yaw=0.0,
                   extra_tags=(), biome="plains", offers=None):
    x, y, z = pos
    tags = ",".join(f'"{t}"' for t in (tag,) + tuple(extra_tags))
    sender.send(f"kill @e[tag={tag}]")
    sender.send(
        f"summon villager {x:.2f} {y:.2f} {z:.2f} "
        f'{{CustomName:{{text:"{esc(name)}",color:"{color(col)}",'
        f'bold:true}},CustomNameVisible:1b,Tags:[{tags}],'
        f"NoAI:1b,Invulnerable:1b,Silent:1b,PersistenceRequired:1b,"
        f'Rotation:[{float(yaw):.1f}f,0f],'
        f'VillagerData:{{profession:"minecraft:{profession}",'
        f'level:5,type:"minecraft:{biome}"}},'
        f"Offers:{{Recipes:[{recipes(offers)}]}}}}")


def set_ai(sender, tag, on):
    """Пуска селянина да се разхожда (on) или го спира на място."""
    sender.send(f"data merge entity @e[tag={tag},limit=1] "
                f"{{NoAI:{0 if on else 1}b}}")


def despawn_npc(sender, char_key):
    tag = f"bvg_{char_key}"
    sender.send(f"kill @e[tag={tag}]")
    sender.send(f"kill @e[tag={tag}_bubble]")


# ---------- четене от света ----------

POS_RE = re.compile(r"\[(-?[\d.]+)d,\s*(-?[\d.]+)d,\s*(-?[\d.]+)d\]")
ROT_RE = re.compile(r"\[(-?[\d.]+)f,\s*(-?[\d.]+)f\]")
NUM_RE = re.compile(r"data:\s*(-?[\d.]+)[bsfdL]?\s*$")
STR_RE = re.compile(r'data:\s*"([^"]+)"')
INV_ID = re.compile(r'id:\s*"minecraft:([a-z0-9_]+)"')
INV_CNT = re.compile(r"count:\s*(\d+)")


def player_pos(sender, player):
    ok, reply = sender.query(f"data get entity {player} Pos")
    if not ok:
        return None
    m = POS_RE.search(reply or "")
    return tuple(float(v) for v in m.groups()) if m else None


def player_yaw(sender, player):
    ok, reply = sender.query(f"data get entity {player} Rotation")
    m = ROT_RE.search(reply or "") if ok else None
    return float(m.group(1)) if m else 0.0


def npc_pos(sender, char_key):
    return tag_pos(sender, f"bvg_{char_key}")


def tag_pos(sender, tag):
    ok, reply = sender.query(f"data get entity @e[tag={tag},limit=1] Pos")
    if not ok:
        return None
    m = POS_RE.search(reply or "")
    return tuple(float(v) for v in m.groups()) if m else None


def distance(a, b):
    if not a or not b:
        return 1e9
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def _items(reply, limit=8):
    """Предметите от NBT отговора: [(име, брой)], най-многото първо."""
    out = {}
    text = reply or ""
    for m in INV_ID.finditer(text):
        start = text.rfind("{", 0, m.start())
        end = text.find("}", m.end())
        chunk = text[start if start >= 0 else 0:end if end > 0 else None]
        c = INV_CNT.search(chunk)
        out[m.group(1)] = out.get(m.group(1), 0) + (int(c.group(1)) if c
                                                     else 1)
    return sorted(out.items(), key=lambda kv: -kv[1])[:limit]


def players_info(sender, names):
    """Всичко полезно за играчите с една обиколка до сървъра."""
    names = [n for n in names if name_ok(n)]
    fields = ("Pos", "Dimension", "Health", "foodLevel", "XpLevel",
              "Inventory", "equipment")
    cmds = [f"data get entity {n} {f}" for n in names for f in fields]
    res = sender.query_many(cmds) if hasattr(sender, "query_many") else \
        [sender.query(c) for c in cmds]
    out = {}
    for i, n in enumerate(names):
        part = res[i * len(fields):(i + 1) * len(fields)]
        if len(part) < len(fields):
            break
        get = {f: (r if ok else "") for f, (ok, r) in zip(fields, part)}
        info = {}
        m = POS_RE.search(get["Pos"])
        if m:
            info["pos"] = tuple(round(float(v), 1) for v in m.groups())
        m = STR_RE.search(get["Dimension"])
        if m:
            info["dim"] = m.group(1).replace("minecraft:", "")
        for key, f in (("health", "Health"), ("food", "foodLevel"),
                       ("level", "XpLevel")):
            m = NUM_RE.search(get[f].strip())
            if m:
                info[key] = round(float(m.group(1)), 1)
        info["items"] = _items(get["Inventory"])
        info["armor"] = [i for i, _ in _items(get["equipment"], 6)]
        if info.get("pos"):
            out[n] = info
    return out


def _num(reply):
    m = re.search(r"(-?\d+)\D*$", (reply or "").strip())
    return int(m.group(1)) if m else None


_TIME_FORM = ["daytime"]          # коя форма на „time query" върви тук


def time_of_day(sender):
    """(час от денонощието 0-24000, номер на деня) или (None, None).

    До 1.21 има „time query daytime/day"; от 26.1 времето е „часовник" и
    се пита с „time query time" (всички тикове) — сметката е наша.
    Помним коя форма е минала, за да не пращаме грешната всеки път.
    """
    order = ["time", "daytime"] if _TIME_FORM[0] == "time" else \
        ["daytime", "time"]
    for form in order:
        if form == "daytime":
            res = sender.query_many(["time query daytime", "time query day"])
            vals = [_num(r) if ok else None for ok, r in res]
            if len(vals) == 2 and None not in vals:
                _TIME_FORM[0] = "daytime"
                return tuple(vals)
        else:
            ok, r = sender.query("time query time")
            total = _num(r) if ok else None
            if total is not None:
                _TIME_FORM[0] = "time"
                return (total % 24000, total // 24000)
    return (None, None)


def surfaces(sender, points):
    """Височината на земята в няколко точки наведнъж.

    Ползва „execute positioned over" — най-горният блок, който спира
    движението (листата не се броят, водата се брои). Връща списък с
    Y на най-горния твърд блок или None, ако мястото не е заредено.
    """
    with _probe_lock:
        pid = next(_probe_ids)
    tags = [f"bvg_h{pid}_{i}" for i in range(len(points))]
    cmds = []
    for (x, z), tag in zip(points, tags):
        cmds.append(
            f"execute positioned {int(x) + 0.5} 0 {int(z) + 0.5} positioned "
            f"over motion_blocking_no_leaves run summon marker ~ ~ ~ "
            f'{{Tags:["{tag}","bvg_h"]}}')
    for tag in tags:
        cmds.append(f"data get entity @e[tag={tag},limit=1] Pos")
    for tag in tags:
        cmds.append(f"kill @e[tag={tag}]")
    res = sender.query_many(cmds)
    out = []
    for ok, r in res[len(points):2 * len(points)]:
        m = POS_RE.search(r or "") if ok else None
        out.append(int(round(float(m.group(2)))) - 1 if m else None)
    return out


def blocks_are(sender, points, block_or_tag):
    """Кои от точките (x, y, z) са този блок/таг."""
    cmds = [f"execute if block {x} {y} {z} {block_or_tag}"
            for x, y, z in points]
    return [ok and "passed" in (r or "").lower()
            for ok, r in sender.query_many(cmds)]


def region_empty(sender, x1, y1, z1, x2, y2, z2):
    """Дали всичко в кутията е въздух — сравнява я с празното небе.

    Така никога не строим върху чужда постройка, дърво или вода.
    """
    x1, x2 = sorted((int(x1), int(x2)))
    y1, y2 = sorted((int(y1), int(y2)))
    z1, z2 = sorted((int(z1), int(z2)))
    h = y2 - y1
    sky = 318 - h
    if y2 >= sky or (x2 - x1 + 1) * (h + 1) * (z2 - z1 + 1) > 32768:
        return False
    ok, r = sender.query(f"execute if blocks {x1} {y1} {z1} {x2} {y2} {z2} "
                         f"{x1} {sky} {z1} all")
    return ok and "passed" in (r or "").lower()


# Тревата и цветята не са препятствие — махаме ги, преди да проверим
VEGETATION = ("minecraft:short_grass", "minecraft:tall_grass",
              "minecraft:fern", "minecraft:large_fern",
              "minecraft:dead_bush", "#minecraft:small_flowers",
              "minecraft:snow", "minecraft:bush", "minecraft:firefly_bush",
              "minecraft:leaf_litter", "minecraft:short_dry_grass",
              "minecraft:tall_dry_grass", "minecraft:sweet_berry_bush",
              "minecraft:sunflower", "minecraft:lilac",
              "minecraft:rose_bush", "minecraft:peony", "minecraft:pink_petals",
              "minecraft:wildflowers")


def clear_vegetation_cmds(x1, y1, z1, x2, y2, z2):
    x1, x2 = sorted((int(x1), int(x2)))
    y1, y2 = sorted((int(y1), int(y2)))
    z1, z2 = sorted((int(z1), int(z2)))
    return [f"fill {x1} {y1} {z1} {x2} {y2} {z2} minecraft:air replace {v}"
            for v in VEGETATION]
