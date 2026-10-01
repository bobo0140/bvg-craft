"""
world.py — помощници за действия в света.

Всичко, което се изпраща към играта, минава оттук, за да има едно
място, където се грижим за кавичките и формата на имената.
"""

import json
import re

from ..ai.characters import CHARACTERS


def esc(text) -> str:
    """Безопасен текст в JSON низ на Minecraft."""
    return json.dumps(str(text), ensure_ascii=False)[1:-1]


def say(sender, char_key, text):
    """Реплика на герой в чата, с цветно име."""
    ch = CHARACTERS[char_key]
    sender.send(
        f'tellraw @a [{{"text":"[{esc(ch["name"])}] ","color":"{ch["color"]}",'
        f'"bold":true}},{{"text":"{esc(text)}","color":"white"}}]')


def whisper(sender, player, char_key, text):
    ch = CHARACTERS[char_key]
    sender.send(
        f'tellraw {player} [{{"text":"[{esc(ch["name"])}] ","color":'
        f'"{ch["color"]}"}},{{"text":"{esc(text)}","color":"gray",'
        f'"italic":true}}]')


def announce(sender, title, subtitle="", color="gold"):
    sender.send('title @a times 10 70 20')
    sender.send(f'title @a title {{"text":"{esc(title)}","color":"{color}",'
                f'"bold":true}}')
    if subtitle:
        sender.send(f'title @a subtitle {{"text":"{esc(subtitle)}",'
                    f'"color":"gray"}}')


def actionbar(sender, target, text, color="yellow"):
    sender.send(f'title {target} actionbar {{"text":"{esc(text)}",'
                f'"color":"{color}"}}')


def sound(sender, target, name, pitch=1.0, volume=1.0):
    sender.send(f"execute at {target} run playsound {name} master "
                f"{target} ~ ~ ~ {volume} {pitch}", optional=True)


def bubble(sender, char_key, text):
    """Балонче с репликата над главата на героя."""
    tag = f"bvg_{char_key}"
    short = esc(str(text)[:90])
    sender.send(f"kill @e[tag={tag}_bubble]")
    sender.send(
        f"execute at @e[tag={tag},limit=1] run summon text_display ~ ~2.4 ~ "
        f'{{text:{{text:"{short}",color:"white"}},billboard:"center",'
        f'Tags:["{tag}_bubble"],background:1073741824,view_range:3.0f,'
        f"line_width:180}}", optional=True)


def spawn_npc(sender, char_key, pos, facing_yaw=0.0):
    """Героят като селянин, който не мърда и не може да бъде убит."""
    ch = CHARACTERS[char_key]
    tag = f"bvg_{char_key}"
    x, y, z = pos
    sender.send(f"kill @e[tag={tag}]")
    sender.send(
        f"summon villager {x:.2f} {y:.2f} {z:.2f} "
        f'{{CustomName:{{text:"{esc(ch["name"])}",color:"{ch["color"]}",'
        f'bold:true}},CustomNameVisible:1b,Tags:["{tag}","bvg_npc"],'
        f"NoAI:1b,Invulnerable:1b,Silent:1b,PersistenceRequired:1b,"
        f'Rotation:[{facing_yaw}f,0f],'
        f'VillagerData:{{profession:"minecraft:{ch["profession"]}",'
        f'level:5,type:"minecraft:plains"}},Offers:{{Recipes:[]}}}}')


def despawn_npc(sender, char_key):
    tag = f"bvg_{char_key}"
    sender.send(f"kill @e[tag={tag}]")
    sender.send(f"kill @e[tag={tag}_bubble]")


POS_RE = re.compile(r"\[(-?[\d.]+)d,\s*(-?[\d.]+)d,\s*(-?[\d.]+)d\]")
ROT_RE = re.compile(r"\[(-?[\d.]+)f,\s*(-?[\d.]+)f\]")


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
    ok, reply = sender.query(
        f"data get entity @e[tag=bvg_{char_key},limit=1] Pos")
    if not ok:
        return None
    m = POS_RE.search(reply or "")
    return tuple(float(v) for v in m.groups()) if m else None


def distance(a, b):
    if not a or not b:
        return 1e9
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5
