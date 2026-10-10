"""
quests.py — задачи, които се следят сами.

Задача дават селяните, духовете или режисьорът. Видове:
  collect — донеси N предмета (взимат се, когато си до този, който я е дал)
  kill    — убий N създания от даден вид
  mine    — изкопай N блока
  craft   — направи N предмета
  visit   — стигни до място

Всичко се проверява на няколко секунди през RCON: инвентарът с
„execute if items", убийствата и изкопаното — със статистиките на играта
(scoreboard). Задачите се пазят в quests.json и оцеляват рестарт.
"""

import hashlib
import json
import math
import os
import re
import threading
import time

from .. import paths
from ..logbus import log
from . import world

FILE = "quests.json"
MAX_PER_PLAYER = 3
NEAR_GIVER = 8
NAME_RE = re.compile(r"^#?(?:minecraft:)?[a-z0-9_]{2,40}$")

KIND_TEXT = {"collect": "Донеси", "kill": "Убий", "mine": "Изкопай",
             "craft": "Направи", "visit": "Стигни до"}
CRITERIA = {"kill": "minecraft.killed:minecraft.{t}",
            "mine": "minecraft.mined:minecraft.{t}",
            "craft": "minecraft.crafted:minecraft.{t}"}

# Готови задачи за селяните по занаят: (вид, цел, брой, награда)
VILLAGER_WANTS = {
    "farmer": [("collect", "wheat", 12, ["emerald 3", "bread 6"]),
               ("collect", "carrot", 10, ["emerald 3", "golden_carrot 3"]),
               ("collect", "pumpkin", 3, ["emerald 4"]),
               ("collect", "bone_meal", 8, ["emerald 2", "cake 1"]),
               ("collect", "sugar_cane", 12, ["emerald 3", "book 2"])],
    "mason": [("collect", "cobblestone", 32, ["emerald 2", "iron_ingot 3"]),
              ("collect", "stone_bricks", 16, ["emerald 3"]),
              ("collect", "iron_ingot", 5, ["emerald 5", "shield 1"]),
              ("mine", "stone", 40, ["emerald 3", "iron_pickaxe 1"]),
              ("collect", "clay_ball", 10, ["emerald 3", "bricks 16"])],
    "toolsmith": [("collect", "coal", 16, ["emerald 3", "torch 32"]),
                  ("collect", "iron_ingot", 6, ["emerald 4", "iron_axe 1"]),
                  ("collect", "copper_ingot", 12, ["emerald 3",
                                                   "lantern 4"])],
    "cartographer": [("collect", "paper", 12, ["emerald 3", "map 1"]),
                     ("collect", "glass_pane", 8, ["emerald 3",
                                                   "spyglass 1"]),
                     ("visit", None, 1, ["emerald 4", "compass 1"])],
    "*": [("kill", "zombie", 5, ["emerald 4", "iron_sword 1"]),
          ("kill", "skeleton", 4, ["emerald 4", "arrow 32"]),
          ("kill", "spider", 3, ["emerald 3", "string 8"]),
          ("collect", "oak_log", 16, ["emerald 2", "apple 4"]),
          ("collect", "leather", 5, ["emerald 4", "saddle 1"]),
          ("collect", "string", 6, ["emerald 2", "bow 1"])],
}

ASK_LINES = ["Ей, {p}! Би ли ми помогнал? {task}. Ще те наградя.",
             "{p}, дете, трябва ми помощ: {task}. Няма да съжаляваш.",
             "Чакай, {p}! {task} — ако можеш, ще ти се отплатя.",
             "{p}, имам работа за теб: {task}."]
THANKS = ["Благодаря ти, {p}! Ето обещаното.", "Ха! Знаех си, че ще се "
          "справиш, {p}.", "Злато момче си, {p}. Заповядай.",
          "Честна работа, честна награда. Благодаря, {p}!"]


def _obj(criterion):
    return "q" + hashlib.md5(criterion.encode()).hexdigest()[:10]


def _short(item):
    return str(item or "").replace("minecraft:", "").lstrip("#")


class Quests:
    def __init__(self, sender, brain):
        self.s = sender
        self.b = brain
        self.items = []               # активните задачи (речници)
        self.done = []                # последните завършени (за таблото)
        self.next_id = 1
        self.next_check = 0.0
        self._lock = threading.RLock()
        self._made_obj = set()
        self.load()

    # ---------- запазване ----------

    @property
    def path(self):
        return os.path.join(paths.DATA, FILE)

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.items = data.get("items", [])
            self.done = data.get("done", [])[-20:]
            self.next_id = data.get("next_id", 1)
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            os.makedirs(paths.DATA, exist_ok=True)
            tmp = self.path + ".tmp"
            with self._lock:
                data = {"items": self.items, "done": self.done[-20:],
                        "next_id": self.next_id}
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError as e:
            log.warn("Задачи", f"Не мога да запазя: {e}")

    def reset(self):
        with self._lock:
            self.items, self.done = [], []
        self.save()

    # ---------- даване ----------

    def active_for(self, player):
        return [q for q in self.items if q["player"] == player]

    def give(self, player, kind, target=None, amount=1, title="", text="",
             reward=None, xp=0, fame=10, giver=None, minutes=30,
             at=None, radius=6):
        """Нова задача. giver: {"name", "color", "kind", "ref"}."""
        if not world.name_ok(player):
            return False, "няма такъв играч"
        if kind not in KIND_TEXT:
            return False, "непознат вид задача"
        if len(self.active_for(player)) >= MAX_PER_PLAYER:
            return False, f"{player} вече има {MAX_PER_PLAYER} задачи"
        try:
            amount = max(1, min(int(amount or 1), 2000))
            minutes = max(3, min(float(minutes or 30), 240))
            fame = max(0, min(int(fame or 0), 200))
            xp = max(0, min(int(xp or 0), 500))
        except (TypeError, ValueError):
            return False, "лоши числа"
        q = {"id": 0, "player": player, "kind": kind, "amount": amount,
             "title": str(title or "")[:48], "text": str(text or "")[:160],
             "reward": [r for r in (reward or []) if world.item_spec(r)][:4],
             "xp": xp, "fame": fame, "giver": giver or {"name": "Режисьорът",
                                                        "color": "gold",
                                                        "kind": "gm"},
             "made": time.time(), "until": time.time() + minutes * 60,
             "progress": 0}
        if kind == "visit":
            try:
                x, z = int(at[0]), int(at[-1])
            except (TypeError, ValueError, IndexError):
                return False, "няма място за посещение"
            q["at"] = [x, z]
            q["radius"] = max(3, min(int(radius or 6), 30))
            label = f"X {x} Z {z}"
        else:
            t = str(target or "").strip().lower()
            if not NAME_RE.match(t):
                return False, f"неразбираема цел ({t})"
            if kind != "collect":
                t = t.lstrip("#")              # за статистиките — без тагове
            q["target"] = t if (t.startswith("#") or ":" in t) else \
                "minecraft:" + t
            label = f"{amount} × {_short(t)}"
            if kind in CRITERIA:
                crit = CRITERIA[kind].format(t=_short(t))
                q["criterion"] = crit
                q["obj"] = _obj(crit)
                ok, err = self._ensure_obj(q["obj"], crit)
                if not ok:
                    return False, f"сървърът не приема {crit} ({err[:60]})"
                q["base"] = self._score(player, q["obj"])
        q["label"] = label
        with self._lock:
            q["id"] = self.next_id
            self.next_id += 1
            self.items.append(q)
        self.save()
        self._announce(q)
        log.info("Задачи", f"{q['giver']['name']} → {player}: "
                           f"{KIND_TEXT[kind]} {label}")
        try:
            self.b.director.note(f"{q['giver']['name']} даде задача на "
                                 f"{player}: {KIND_TEXT[kind]} {label}")
        except Exception:
            pass
        return True, f"{KIND_TEXT[kind]} {label}"

    def _ensure_obj(self, obj, crit):
        if obj in self._made_obj:
            return True, ""
        from ..server.rcon import classify_reply
        ok, r = self.s.query(f'scoreboard objectives add {obj} {crit} '
                             f'{{"text":"задача"}}')
        if ok and (classify_reply(r) == "ok" or "already" in r.lower()):
            self._made_obj.add(obj)
            return True, ""
        return False, r or "няма връзка"

    def _score(self, player, obj):
        ok, r = self.s.query(f"scoreboard players get {player} {obj}")
        m = re.search(r"\bhas\s+(\d+)", r or "", re.I) if ok else None
        return int(m.group(1)) if m else 0

    def _reward_text(self, q):
        parts = [f"{n}× {i}" for i, n in
                 (world.item_spec(r) for r in q["reward"])]
        if q["fame"]:
            parts.append(f"{q['fame']} слава")
        return ", ".join(parts) or "благодарност"

    def _announce(self, q):
        g = q["giver"]
        task = (f"{KIND_TEXT[q['kind']]} {q['label']}")
        body = q["text"] or task
        self.s.send(
            f'tellraw {q["player"]} ["",{{"text":"📜 Задача от '
            f'{world.esc(g["name"])}: ","color":"{world.color(g.get("color"), "gold")}",'
            f'"bold":true}},{{"text":"{world.esc(body)}","color":"white"}},'
            f'{{"text":"\\n   Цел: {world.esc(task)} · Награда: '
            f'{world.esc(self._reward_text(q))}","color":"gray"}}]')
        world.sound(self.s, q["player"], "minecraft:item.book.page_turn")
        world.actionbar(self.s, q["player"], f"Нова задача: {task}", "gold")

    # ---------- проверка ----------

    def tick(self, positions, givers):
        """positions: {играч: (x,y,z)}; givers: {ref: (x,y,z)}."""
        now = time.time()
        if now < self.next_check:
            return
        self.next_check = now + 5
        online = set(self.b.server.online)
        with self._lock:
            mine = [q for q in self.items if q["player"] in online]
            expired = [q for q in self.items if q["until"] < now]
        for q in expired:
            self._drop(q, expired=True)
        cmds, refs = [], []
        for q in mine:
            if q in expired:
                continue
            if q["kind"] == "collect":
                cmds.append(f"execute if items entity {q['player']} "
                            f"container.* {q['target']}")
                refs.append(q)
            elif q["kind"] in CRITERIA:
                cmds.append(f"scoreboard players get {q['player']} "
                            f"{q['obj']}")
                refs.append(q)
        res = self.s.query_many(cmds) if cmds else []
        for q, (ok, r) in zip(refs, res):
            if not ok:
                continue
            if q["kind"] == "collect":
                m = re.search(r"count:\s*(\d+)", r or "", re.I)
                have = int(m.group(1)) if m else 0
            else:
                m = re.search(r"\bhas\s+(\d+)", r or "", re.I)
                have = max(0, (int(m.group(1)) if m else 0) - q.get("base", 0))
            self._progress(q, have, positions, givers)
        for q in mine:
            if q["kind"] == "visit":
                p = positions.get(q["player"])
                if p and math.hypot(p[0] - q["at"][0],
                                    p[2] - q["at"][1]) <= q["radius"]:
                    self._complete(q)

    def _progress(self, q, have, positions, givers):
        done = have >= q["amount"]
        if have != q.get("progress"):
            q["progress"] = have
            if not done:
                world.actionbar(self.s, q["player"],
                                f"{KIND_TEXT[q['kind']]} {_short(q.get('target'))}"
                                f": {min(have, q['amount'])}/{q['amount']}",
                                "yellow")
        if not done:
            return
        if q["kind"] == "collect":
            gpos = givers.get(q["giver"].get("ref")) if q["giver"].get("ref") \
                else None
            ppos = positions.get(q["player"])
            if gpos and ppos and world.distance(gpos, ppos) > NEAR_GIVER:
                if time.time() - q.get("hint_at", 0) > 25:
                    q["hint_at"] = time.time()
                    world.actionbar(self.s, q["player"],
                                    f"Имаш всичко! Занеси го на "
                                    f"{q['giver']['name']}.", "green")
                return
            if gpos is None and q["giver"].get("ref") and \
                    q["giver"].get("kind") == "villager":
                return                  # селянинът го няма — чакаме
            self.s.send(f"clear {q['player']} {q['target']} {q['amount']}")
        self._complete(q)

    def _complete(self, q):
        p = q["player"]
        with self._lock:
            if q not in self.items:
                return
            self.items.remove(q)
            q["finished"] = time.time()
            self.done.append(q)
        world.reward(self.s, p, q["reward"], q["xp"],
                     title="📜 Задачата е изпълнена",
                     reason=q["title"] or q["label"])
        g = q["giver"]
        line = THANKS[q["id"] % len(THANKS)].format(p=p)
        world.speak(self.s, g["name"], g.get("color", "gold"), line)
        if q["fame"]:
            self.b.fame.add(p, q["fame"], f"задача: {q['label']}")
        info = self.b.player(p)
        info["quests"] = info.get("quests", 0) + 1
        self.b._save_players()
        log.ok("Задачи", f"{p} изпълни: {KIND_TEXT[q['kind']]} {q['label']}")
        try:
            self.b.director.note(f"{p} изпълни задача от {g['name']}: "
                                 f"{q['label']}", important=False)
        except Exception:
            pass
        self.save()

    def _drop(self, q, expired=False):
        with self._lock:
            if q not in self.items:
                return
            self.items.remove(q)
        if expired and q["player"] in self.b.server.online:
            world.tell(self.s, q["player"], f"Задачата „{q['label']}“ от "
                                            f"{q['giver']['name']} изтече.")
        self.save()

    def cancel(self, qid):
        q = next((q for q in self.items if q["id"] == qid), None)
        if q:
            self._drop(q)
        return bool(q)

    # ---------- за играча, режисьора и таблото ----------

    def describe(self, player):
        qs = self.active_for(player)
        if not qs:
            return "Нямаш задачи. Мини покрай някой селянин — все ще иска нещо."
        out = []
        for q in qs:
            left = int((q["until"] - time.time()) // 60)
            out.append(f"{KIND_TEXT[q['kind']]} {q['label']} "
                       f"({q.get('progress', 0)}/{q['amount']}) за "
                       f"{q['giver']['name']}, остават {left} мин")
        return " | ".join(out)

    def status(self):
        now = time.time()
        return {"active": [{"id": q["id"], "player": q["player"],
                            "kind": KIND_TEXT[q["kind"]],
                            "label": q["label"], "giver": q["giver"]["name"],
                            "progress": q.get("progress", 0),
                            "amount": q["amount"],
                            "left": int(q["until"] - now)}
                           for q in self.items],
                "done": [{"player": q["player"], "label": q["label"],
                          "giver": q["giver"]["name"],
                          "ago": int(now - q.get("finished", now))}
                         for q in self.done[-6:]][::-1]}

    def summary(self):
        return [f"{q['player']}: {KIND_TEXT[q['kind']]} {q['label']} "
                f"({q.get('progress', 0)}/{q['amount']}) от "
                f"{q['giver']['name']}" for q in self.items[:10]]
