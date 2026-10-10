"""
events_lib.py — събитията на сървъра.

Всяко събитие има начало, проверка на всеки няколко секунди, условие за
победа и край. Работят и без AI — тогава Пазителят ползва готовите си
реплики. С AI само говори по-шарено.
"""

import math
import random
import re
import time

from ..ai.characters import CHARACTERS
from . import world

REWARD_SMALL = ["diamond 2", "gold_ingot 6", "emerald 4", "golden_apple 1"]
REWARD_BIG = ["diamond 6", "netherite_scrap 2", "enchanted_golden_apple 1",
              "totem_of_undying 1"]

RIDDLES = [
    ("Колкото повече взимаш от мен, толкова по-голяма ставам. Какво съм?",
     ["дупка", "яма"]),
    ("Има зъби, но не хапе. Какво е?", ["гребен", "гребенче"]),
    ("Пада, но никога не се удря. Какво е?", ["нощ", "нощта", "здрач"]),
    ("Има очи, но не вижда. Какво е?", ["картоф", "игла"]),
    ("Колкото повече има от него, толкова по-малко виждаш. Какво е?",
     ["тъмнина", "мрак", "тъмнината"]),
    ("В Minecraft: зелен е, тих е и прави 'ссс'. Кой е?",
     ["крийпър", "creeper", "крипър"]),
    ("Има глава и опашка, но няма тяло. Какво е?", ["монета", "пара"]),
    ("Тече, но няма крака. Какво е?", ["вода", "река"]),
]


def give(sender, player, reward):
    sender.send(f"give {player} {reward}")


class Event:
    key = "base"
    title = ""
    min_players = 1
    duration = 120

    def __init__(self, engine):
        self.e = engine
        self.started = time.time()
        self.done = False

    @property
    def s(self):
        return self.e.sender

    def players(self):
        return sorted(self.e.online())

    def start(self):
        pass

    def tick(self):
        if time.time() - self.started > self.duration:
            self.finish(None)

    def on_event(self, ev):
        pass

    def finish(self, winner):
        self.done = True


class MeteorShower(Event):
    key = "meteors"
    title = "МЕТЕОРИТЕН ДЪЖД"
    duration = 45

    def start(self):
        world.announce(self.s, "☄ МЕТЕОРИТЕН ДЪЖД", "Вътре може да има руда!",
                       "red")
        self.left = 4 + 2 * self.e.cfg.get("chaos")
        self.next_at = time.time()

    def tick(self):
        if self.left > 0 and time.time() >= self.next_at:
            self.left -= 1
            self.next_at = time.time() + random.uniform(3, 6)
            p = random.choice(self.players())
            dx, dz = random.randint(-25, 25), random.randint(-25, 25)
            core = random.choices(
                ["diamond_ore", "emerald_ore", "gold_ore", "magma_block",
                 "ancient_debris"], [3, 3, 5, 6, 1])[0]
            for i, (ox, oz) in enumerate([(0, 0), (1, 0), (-1, 0),
                                          (0, 1), (0, -1)]):
                block = core if i == 0 else random.choice(
                    ["magma_block", "obsidian", "blackstone"])
                self.s.send(
                    f"execute at {p} run summon falling_block "
                    f"~{dx + ox} ~45 ~{dz + oz} "
                    f'{{BlockState:{{Name:"minecraft:{block}"}},Time:1,'
                    f"DropItem:0b,HurtEntities:0b}}")
            self.s.send(f"execute at {p} run particle minecraft:flame "
                        f"~{dx} ~40 ~{dz} 1 8 1 0.05 120", optional=True)
            world.sound(self.s, p, "minecraft:entity.generic.explode", 0.6)
        super().tick()

    def finish(self, winner):
        world.say(self.s, "keeper",
                  "Небето утихна. Търсете метеоритите — някои крият руда.")
        self.done = True


class Bounty(Event):
    key = "bounty"
    title = "ЛОВ НА ГЛАВИ"
    min_players = 2
    duration = 300

    def start(self):
        self.target = random.choice(self.players())
        self.s.send(f"effect give {self.target} minecraft:glowing "
                    f"{self.duration} 0 true")
        world.announce(self.s, "🎯 ЛОВ НА ГЛАВИ",
                       f"Наградата е за {self.target}", "red")
        world.say(self.s, "keeper",
                  f"{self.target} свети. Който го победи — печели. "
                  f"Ако оцелее 5 минути, наградата е негова.")

    def on_event(self, ev):
        if ev["type"] == "death" and ev["player"] == self.target:
            killer = ev.get("killer")
            if killer and killer in self.e.online() and killer != self.target:
                self.finish(killer)
            else:
                world.say(self.s, "keeper",
                          f"{self.target} падна, но не от ловец. Ловът "
                          f"продължава!")
        elif ev["type"] == "leave" and ev["player"] == self.target:
            world.say(self.s, "keeper", f"{self.target} избяга от света. "
                                        f"Страхливо.")
            self.done = True

    def tick(self):
        if time.time() - self.started > self.duration:
            self.finish(self.target, survived=True)

    def finish(self, winner, survived=False):
        self.s.send(f"effect clear {self.target} minecraft:glowing")
        if winner:
            for r in REWARD_BIG[:2]:
                give(self.s, winner, r)
            msg = (f"{winner} оцеля срещу всички!" if survived
                   else f"{winner} хвана {self.target}!")
            world.announce(self.s, "🏆 " + msg, "", "gold")
        self.done = True


class Treasure(Event):
    key = "treasure"
    title = "СЪКРОВИЩЕ"
    duration = 360

    def start(self):
        anchor = random.choice(self.players())
        pos = world.player_pos(self.s, anchor)
        if not pos:
            self.done = True
            return
        ang = random.uniform(0, math.tau)
        dist = random.randint(50, 110)
        self.x = int(pos[0] + math.cos(ang) * dist)
        self.z = int(pos[2] + math.sin(ang) * dist)
        self.last_hint = 0
        self.chest = None
        try:
            y = world.surfaces(self.s, [(self.x, self.z)])[0]
        except Exception:
            y = None
        if y is not None and -60 < y < 300:
            loot = random.sample(REWARD_BIG + REWARD_SMALL, 4)
            items = ",".join(
                f'{{Slot:{i * 2}b,id:"minecraft:{it.split()[0]}",'
                f'count:{int(it.split()[1])}}}' for i, it in enumerate(loot))
            self.chest = (self.x, y + 1, self.z)
            self.s.send(f"setblock {self.x} {y + 1} {self.z} "
                        f"minecraft:chest[facing=north]{{Items:[{items}]}}")
        world.announce(self.s, "💎 СЪКРОВИЩЕ", "Някъде наблизо е скрито...")
        world.say(self.s, "keeper",
                  "Зарових сандък. Ще ви казвам кой е топъл и кой студен.")

    def tick(self):
        if self.done:
            return
        best, best_d = None, 1e9
        for p in self.players():
            pos = world.player_pos(self.s, p)
            if not pos:
                continue
            d = math.hypot(pos[0] - self.x, pos[2] - self.z)
            if d < best_d:
                best, best_d = p, d
            if d < 4:
                self.finish(p)
                return
        if best and time.time() - self.last_hint > 20:
            self.last_hint = time.time()
            heat = ("ГОРИШ!" if best_d < 15 else "топло" if best_d < 40
                    else "хладно" if best_d < 80 else "леденo")
            for p in self.players():
                world.actionbar(self.s, p, f"Съкровище: {best} е най-близо "
                                           f"— {heat}", "aqua")
            self.s.send(f"particle minecraft:end_rod {self.x} 200 {self.z} "
                        f"0.2 60 0.2 0 200 force", optional=True)
        super().tick()

    def finish(self, winner):
        if winner:
            if not self.chest:
                for r in REWARD_BIG:
                    give(self.s, winner, r)
            world.announce(self.s, f"💎 {winner} намери съкровището!",
                           "Сандъкът е твой" if self.chest else "", "aqua")
        else:
            world.say(self.s, "keeper", "Никой не го намери. Остава заровено "
                                        "завинаги... или до следващия път.")
        self.done = True


class NightOfTheDead(Event):
    key = "undead"
    title = "НОЩ НА МЪРТВИТЕ"
    duration = 180

    def start(self):
        self.dead = set()
        self.s.send("time set midnight")   # име: върви и на 1.21, и на 26.x
        self.s.send("weather thunder 200")
        per = 2 + self.e.cfg.get("chaos")
        for p in self.players():
            for i in range(per):
                kind = random.choice(["zombie", "skeleton", "husk", "stray"])
                self.s.send(
                    f"execute at {p} run summon {kind} ~{random.randint(-12, 12)}"
                    f" ~2 ~{random.randint(-12, 12)} "
                    f'{{Tags:["bvg_undead"],PersistenceRequired:1b}}')
        world.announce(self.s, "☠ НОЩ НА МЪРТВИТЕ",
                       "Оцелейте 3 минути", "dark_red")

    def on_event(self, ev):
        if ev["type"] == "death":
            self.dead.add(ev["player"])

    def finish(self, winner):
        survivors = [p for p in self.players() if p not in self.dead]
        for p in survivors:
            give(self.s, p, random.choice(REWARD_SMALL))
        self.s.send("kill @e[tag=bvg_undead]")
        self.s.send("time set day")
        self.s.send("weather clear")
        world.say(self.s, "keeper",
                  ("Оцелели: " + ", ".join(survivors)) if survivors
                  else "Никой не оцеля. Трагично. И смешно.")
        self.done = True


class LowGravity(Event):
    key = "gravity"
    title = "ГРАВИТАЦИЯТА СЕ СЧУПИ"
    duration = 40

    def start(self):
        self.s.send("effect give @a minecraft:jump_boost 40 4 true")
        self.s.send("effect give @a minecraft:slow_falling 40 0 true")
        self.s.send("execute at @a run particle minecraft:reverse_portal "
                    "~ ~1 ~ 3 2 3 0.05 200", optional=True)
        world.announce(self.s, "🌙 ГРАВИТАЦИЯТА СЕ СЧУПИ", "40 секунди",
                       "light_purple")

    def finish(self, winner):
        world.say(self.s, "keeper", "Гравитацията се върна. Съжалявам.")
        self.done = True


class ChickenRain(Event):
    key = "chickens"
    title = "ВАЛИ ПИЛЕТА"
    duration = 25

    def start(self):
        n = 6 + 3 * self.e.cfg.get("chaos")
        for p in self.players():
            for _ in range(n):
                self.s.send(
                    f"execute at {p} run summon chicken "
                    f"~{random.randint(-8, 8)} ~{random.randint(12, 25)} "
                    f"~{random.randint(-8, 8)}", optional=True)
        world.announce(self.s, "🐔 ВАЛИ ПИЛЕТА", "", "yellow")
        world.say(self.s, "trickster", "ХАХАХА! Това беше моя идея!")


class Giant(Event):
    key = "giant"
    title = "ГИГАНТЪТ"
    duration = 300
    NAMES = ["Грохот", "Костотрошача", "Мъглявия", "Старият Кал",
             "Тежкия Борис", "Сянката"]

    def start(self):
        self.target = random.choice(self.players())
        self.name = random.choice(self.NAMES)
        hp = 120 + 60 * self.e.cfg.get("chaos")
        self.s.send(
            f"execute at {self.target} run summon zombie ~8 ~1 ~8 "
            f'{{CustomName:{{text:"{world.esc(self.name)}",color:"dark_red",'
            f'bold:true}},CustomNameVisible:1b,Tags:["bvg_giant"],'
            f"PersistenceRequired:1b,Health:{hp}f,"
            f'attributes:[{{id:"minecraft:scale",base:3.0}},'
            f'{{id:"minecraft:max_health",base:{hp}}},'
            f'{{id:"minecraft:attack_damage",base:7}}]}}')
        self.s.send("effect give @e[tag=bvg_giant] minecraft:fire_resistance "
                    "9999 0 true")
        world.announce(self.s, f"👹 {self.name.upper()}",
                       "Гигант се появи!", "dark_red")

    def tick(self):
        ok, reply = self.s.query("execute if entity @e[tag=bvg_giant]")
        if ok and "passed" not in (reply or "").lower():
            # гигантът го няма — който е най-близо, печели
            best, best_d = None, 1e9
            last = getattr(self, "last_pos", None)
            for p in self.players():
                d = world.distance(world.player_pos(self.s, p), last)
                if d < best_d:
                    best, best_d = p, d
            self.finish(best if best_d < 30 else None)
            return
        ok, reply = self.s.query("data get entity @e[tag=bvg_giant,limit=1] Pos")
        m = world.POS_RE.search(reply or "") if ok else None
        if m:
            self.last_pos = tuple(float(v) for v in m.groups())
        super().tick()

    def finish(self, winner):
        self.s.send("kill @e[tag=bvg_giant]")
        if winner:
            for r in REWARD_BIG:
                give(self.s, winner, r)
            world.announce(self.s, f"⚔ {winner} повали {self.name}!", "",
                           "gold")
        else:
            world.say(self.s, "keeper", f"{self.name} си тръгна непобеден.")
        self.done = True


class Riddle(Event):
    key = "riddle"
    title = "ГАТАНКА"
    duration = 120

    def __init__(self, engine, question=None, answers=None):
        super().__init__(engine)
        if question and answers:
            self.q, self.answers = question, [a.lower() for a in answers]
        else:
            self.q, ans = random.choice(RIDDLES)
            self.answers = [a.lower() for a in ans]

    def start(self):
        world.announce(self.s, "❓ ГАТАНКА", "Първият верен отговор печели")
        world.say(self.s, "keeper", self.q)

    def on_event(self, ev):
        if ev["type"] != "chat":
            return
        msg = ev["message"].lower().strip(" .!?")
        if any(a in msg.split() or msg == a for a in self.answers):
            self.finish(ev["player"])

    def finish(self, winner):
        if winner:
            give(self.s, winner, random.choice(REWARD_SMALL))
            world.say(self.s, "keeper",
                      f"{winner} позна — {self.answers[0]}! Награда за ума.")
        else:
            world.say(self.s, "keeper",
                      f"Никой не позна. Отговорът беше: {self.answers[0]}.")
        self.done = True


class Race(Event):
    key = "race"
    title = "СЪСТЕЗАНИЕ"
    min_players = 2
    duration = 240

    def start(self):
        anchor = random.choice(self.players())
        pos = world.player_pos(self.s, anchor)
        if not pos:
            self.done = True
            return
        ang = random.uniform(0, math.tau)
        d = random.randint(80, 150)
        self.x = int(pos[0] + math.cos(ang) * d)
        self.z = int(pos[2] + math.sin(ang) * d)
        world.announce(self.s, "🏁 СЪСТЕЗАНИЕ", f"До X {self.x}  Z {self.z}",
                       "green")
        world.say(self.s, "keeper", f"Първият до X={self.x}, Z={self.z} "
                                    f"печели. Тръгвайте!")
        self.s.send("effect give @a minecraft:speed 15 1 true")

    def tick(self):
        if self.done:
            return
        for p in self.players():
            pos = world.player_pos(self.s, p)
            if pos and math.hypot(pos[0] - self.x, pos[2] - self.z) < 5:
                self.finish(p)
                return
        self.s.send(f"particle minecraft:totem_of_undying {self.x} 200 "
                    f"{self.z} 0.3 60 0.3 0 150 force", optional=True)
        super().tick()

    def finish(self, winner):
        if winner:
            give(self.s, winner, REWARD_BIG[0])
            world.announce(self.s, f"🏁 {winner} спечели!", "", "green")
        else:
            world.say(self.s, "keeper", "Никой не стигна. Мързеливци.")
        self.done = True


class Contest(Event):
    """Състезание с класация отстрани на екрана.

    kind="score": брои по статистика на играта (изкопани, убити,
    скокове...) през scoreboard. kind="items": брои колко от даден
    предмет е събрал играчът от началото насам.
    """
    key = "contest"
    title = "СЪСТЕЗАНИЕ"
    OBJ = "bvgq"
    CRIT = re.compile(r"^minecraft\.(mined|killed|crafted|used|broken|"
                      r"picked_up|dropped|killed_by|custom):minecraft\."
                      r"[a-z0-9_]+$")
    ITEM = re.compile(r"^#?(?:minecraft:)?[a-z0-9_]+$")

    def __init__(self, engine, kind="score", criterion=None, item=None,
                 amount=10, minutes=5, title=None, desc=None, reward=None,
                 xp=60, preset=None):
        super().__init__(engine)
        self.kind = "items" if kind == "items" else "score"
        self.criterion = (criterion or "").strip()
        self.item = (item or "").strip().lower()
        if self.item and not self.item.startswith("#") and \
                ":" not in self.item:
            self.item = "minecraft:" + self.item
        self.amount = max(1, min(int(amount or 10), 100000))
        self.duration = max(60, min(int(float(minutes or 5) * 60), 1800))
        self.title = (title or "СЪСТЕЗАНИЕ")[:40]
        self.desc = (desc or "")[:80]
        self.reward = [r for r in (reward or ["diamond 2"])][:4]
        self.xp = max(0, min(int(xp or 0), 500))
        self.preset = preset
        self.base = {}
        self.scores = {}
        self.next_poll = 0
        self.error = None

    def valid(self):
        if self.kind == "score":
            return bool(self.CRIT.match(self.criterion))
        return bool(self.ITEM.match(self.item))

    def start(self):
        if not self.valid():
            self.error = "неразбираемо състезание"
            self.done = True
            return
        if self.kind == "score":
            res = self.s.query_many([
                f"scoreboard objectives remove {self.OBJ}",
                f"scoreboard objectives add {self.OBJ} {self.criterion} "
                f'{{"text":"{world.esc(self.title)}","color":"gold"}}',
                f"scoreboard objectives setdisplay sidebar {self.OBJ}"])
            from ..server.rcon import classify_reply
            if len(res) < 2 or not res[1][0] or \
                    classify_reply(res[1][1]) != "ok":
                self.error = (res[1][1] if len(res) > 1 else "")[:120]
                self.done = True
                return
        else:
            self.base = self._counts()
        world.announce(self.s, "🏆 " + self.title,
                       self.desc or f"Първият до {self.amount} печели", "gold")
        world.say(self.s, "keeper",
                  f"Състезание: {self.desc or self.title}. Първият до "
                  f"{self.amount} печели! Имате {self.duration // 60} мин.")

    def _counts(self):
        ps = self.players()
        if self.kind == "score":
            cmds = [f"scoreboard players get {p} {self.OBJ}" for p in ps]
        else:
            cmds = [f"execute if items entity {p} container.* {self.item}"
                    for p in ps]
        out = {}
        for p, (ok, r) in zip(ps, self.s.query_many(cmds)):
            # „X has 5 [..]" (scoreboard) или „Test passed. Count: 5" (items)
            m = re.search(r"(?:\bhas|count:)\s*(\d+)", r or "", re.I) \
                if ok else None
            out[p] = int(m.group(1)) if m else 0
        return out

    def tick(self):
        if self.done or time.time() < self.next_poll:
            return
        self.next_poll = time.time() + 4
        now = self._counts()
        if self.kind == "items":
            now = {p: max(0, v - self.base.setdefault(p, v))
                   for p, v in now.items()}
        self.scores.update(now)
        if self.scores:
            lead, val = max(self.scores.items(), key=lambda kv: kv[1])
            if val >= self.amount:
                self.finish(lead)
                return
            if self.kind == "items":
                for p in self.players():
                    world.actionbar(self.s, p, f"{self.title}: ти {now.get(p, 0)}"
                                    f"/{self.amount} · води {lead} ({val})",
                                    "gold")
        if time.time() - self.started > self.duration:
            best = max(self.scores.items(), key=lambda kv: kv[1],
                       default=(None, 0))
            self.finish(best[0] if best[1] > 0 else None)

    def finish(self, winner):
        if self.done:
            return
        self.done = True
        if self.kind == "score":
            self.s.send(f"scoreboard objectives remove {self.OBJ}")
        if winner:
            world.reward(self.s, winner, self.reward, self.xp,
                         title="🏆 ПОБЕДА!", reason=self.title)
            world.announce(self.s, f"🏆 {winner} спечели!", self.title,
                           "gold")
            self.e.won(winner, self.title)
        else:
            world.say(self.s, "keeper", f"„{self.title}“ свърши без "
                                        f"победител. Следващия път!")

    def standings(self):
        return sorted(self.scores.items(), key=lambda kv: -kv[1])[:5]


# Готови състезания — за режисьора без AI, за таблото и за !състезание
CONTESTS = {
    "jumps": dict(kind="score", criterion="minecraft.custom:minecraft.jump",
                  amount=150, minutes=3, title="Скачачи",
                  desc="Най-много скокове за 3 минути",
                  reward=["emerald 4", "cake 1"]),
    "diamonds": dict(kind="items", item="minecraft:diamond", amount=3,
                     minutes=12, title="Диамантена треска",
                     desc="Първият с 3 нови диаманта",
                     reward=["diamond 3", "golden_apple 2"], xp=120),
    "wood": dict(kind="items", item="#minecraft:logs", amount=48, minutes=6,
                 title="Дървосекачи", desc="Първият с 48 дървени трупа",
                 reward=["iron_axe 1", "iron_ingot 8"]),
    "zombies": dict(kind="score",
                    criterion="minecraft.killed:minecraft.zombie", amount=5,
                    minutes=8, title="Лов на зомбита",
                    desc="Първият с 5 убити зомбита",
                    reward=["diamond 2", "golden_apple 1"], xp=100),
    "miner": dict(kind="score", criterion="minecraft.mined:minecraft.stone",
                  amount=64, minutes=5, title="Миньори",
                  desc="Първият с 64 изкопан камък",
                  reward=["iron_pickaxe 1", "torch 32"]),
    "fishing": dict(kind="score",
                    criterion="minecraft.custom:minecraft.fish_caught",
                    amount=3, minutes=7, title="Рибари",
                    desc="Първият с 3 улова",
                    reward=["emerald 6", "cooked_salmon 8"]),
    "farmer": dict(kind="score", criterion="minecraft.mined:minecraft.wheat",
                   amount=24, minutes=6, title="Жътва",
                   desc="Първият с 24 ожънати жита",
                   reward=["bread 16", "emerald 4"]),
    "breeder": dict(kind="score",
                    criterion="minecraft.custom:minecraft.animals_bred",
                    amount=4, minutes=6, title="Фермери",
                    desc="Първият развъдил 4 животни",
                    reward=["golden_carrot 8", "emerald 4"]),
    "runner": dict(kind="score",
                   criterion="minecraft.custom:minecraft.sprint_one_cm",
                   amount=40000, minutes=4, title="Спринт",
                   desc="Най-много спринт (в сантиметри)",
                   reward=["feather 8", "emerald 4"]),
}
CONTEST_NAMES = {k: v["title"] for k, v in CONTESTS.items()}



# ---------------------------------------------------------------------------
# Босове: създания с име, броня, фази и лента с живота горе на екрана
# ---------------------------------------------------------------------------

BOSS_MOBS = {"zombie", "husk", "drowned", "skeleton", "stray", "bogged",
             "wither_skeleton", "piglin_brute", "vindicator", "pillager",
             "evoker", "ravager", "spider", "witch", "blaze",
             "zombified_piglin", "cave_spider"}
MINION_MOBS = {"zombie", "skeleton", "spider", "husk", "stray", "silverfish",
               "vex", "cave_spider", "drowned", "bogged", "pillager",
               "zombified_piglin", "slime", "magma_cube"}
SLOT_OF = (("_helmet", "head"), ("_head", "head"), ("_skull", "head"),
           ("_chestplate", "chest"), ("_leggings", "legs"),
           ("_boots", "feet"))
ID_RE = re.compile(r"^(?:minecraft:)?([a-z0-9_]{2,40})$")

BOSS_PRESETS = [
    {"name": "Грохот Каменния", "mob": "zombie", "scale": 2.6, "hp": 320,
     "damage": 9, "speed": 0.26, "color": "dark_red",
     "armor": ["netherite_helmet", "iron_chestplate"], "weapon": "stone_axe",
     "minions": {"mob": "zombie", "count": 3},
     "reward": ["diamond 4", "golden_apple 2"],
     "taunts": ["Ще ви смачкам като мравки!", "Земята трепери от мен!",
                "Още ли сте живи?!"]},
    {"name": "Кралицата на паяците", "mob": "spider", "scale": 3.0,
     "hp": 260, "damage": 7, "speed": 0.34, "color": "dark_purple",
     "minions": {"mob": "cave_spider", "count": 4},
     "reward": ["diamond 3", "string 32", "golden_apple 1"],
     "taunts": ["Мрежата ми е навсякъде...", "Децата ми са гладни!"]},
    {"name": "Сенчестият стрелец", "mob": "skeleton", "scale": 1.8,
     "hp": 220, "damage": 6, "speed": 0.3, "color": "dark_gray",
     "armor": ["chainmail_helmet", "chainmail_chestplate"], "weapon": "bow",
     "minions": {"mob": "stray", "count": 3},
     "reward": ["diamond 3", "arrow 64", "bow 1"],
     "taunts": ["Не можеш да се скриеш от мен.", "Стрелите ми не пропускат."]},
    {"name": "Борил Нашественика", "mob": "vindicator", "scale": 1.7,
     "hp": 280, "damage": 10, "speed": 0.33, "color": "gray",
     "armor": ["iron_helmet"], "weapon": "diamond_axe",
     "minions": {"mob": "pillager", "count": 3},
     "reward": ["diamond 4", "emerald 12", "totem_of_undying 1"],
     "taunts": ["Селото е мое!", "Брадвата ми е жадна!"]},
    {"name": "Гнилия крал", "mob": "husk", "scale": 2.2, "hp": 300,
     "damage": 8, "speed": 0.27, "color": "gold",
     "armor": ["golden_helmet", "golden_chestplate"], "weapon": "golden_sword",
     "minions": {"mob": "husk", "count": 4},
     "reward": ["gold_ingot 12", "diamond 3", "golden_apple 2"],
     "taunts": ["Короната ми е от кости!", "Пустинята ще ви погълне!"]},
]


def boss_spec(raw):
    """Проверява описанието на бос (от AI или готово). -> чист речник."""
    raw = dict(raw or {})
    mob = str(raw.get("mob", "zombie")).replace("minecraft:", "").lower()
    if mob not in BOSS_MOBS:
        mob = "zombie"

    def num(k, lo, hi, d):
        try:
            return max(lo, min(float(raw.get(k, d)), hi))
        except (TypeError, ValueError):
            return d
    spec = {"name": str(raw.get("name") or "Безименния")[:28],
            "mob": mob, "scale": num("scale", 0.8, 4.0, 2.0),
            "hp": int(num("hp", 60, 800, 250)),
            "damage": num("damage", 2, 20, 8),
            "speed": num("speed", 0.15, 0.45, 0.28),
            "color": world.color(raw.get("color"), "dark_red"),
            "armor": [], "weapon": None,
            "reward": [r for r in (raw.get("reward") or ["diamond 3"])
                       if world.item_spec(r)][:4] or ["diamond 3"],
            "taunts": [str(t)[:90] for t in (raw.get("taunts") or [])][:5],
            "minions": None}
    for a in (raw.get("armor") or [])[:4]:
        m = ID_RE.match(str(a).lower())
        if m and any(m.group(1).endswith(sfx) for sfx, _ in SLOT_OF):
            spec["armor"].append(m.group(1))
    w = ID_RE.match(str(raw.get("weapon") or "").lower())
    if w:
        spec["weapon"] = w.group(1)
    mn = raw.get("minions") or {}
    if isinstance(mn, dict):
        mm = str(mn.get("mob", "")).replace("minecraft:", "").lower()
        if mm in MINION_MOBS:
            try:
                spec["minions"] = {"mob": mm,
                                   "count": max(1, min(int(mn.get("count", 3)),
                                                       6))}
            except (TypeError, ValueError):
                pass
    if not any(slot == "head" for a in spec["armor"]
               for sfx, slot in SLOT_OF if a.endswith(sfx)) and \
            mob in ("zombie", "skeleton", "stray", "drowned", "bogged"):
        spec["armor"].insert(0, "iron_helmet")     # да не изгори на слънце
    return spec


def boss_nbt(spec):
    hp = spec["hp"]
    attrs = [("max_health", hp), ("scale", spec["scale"]),
             ("attack_damage", spec["damage"]),
             ("movement_speed", spec["speed"]),
             ("knockback_resistance", 0.7), ("follow_range", 48)]
    a = ",".join(f'{{id:"minecraft:{k}",base:{v}}}' for k, v in attrs)
    eq = []
    for item in spec["armor"]:
        slot = next(sl for sfx, sl in SLOT_OF if item.endswith(sfx))
        eq.append(f'{slot}:{{id:"minecraft:{item}",count:1}}')
    if spec["weapon"]:
        eq.append(f'mainhand:{{id:"minecraft:{spec["weapon"]}",count:1}}')
    equip = f",equipment:{{{','.join(eq)}}}" if eq else ""
    return (f'{{CustomName:{{text:"{world.esc(spec["name"])}",'
            f'color:"{spec["color"]}",bold:true}},CustomNameVisible:1b,'
            f'Tags:["bvg_boss"],PersistenceRequired:1b,Health:{hp}f,'
            f"attributes:[{a}]{equip},drop_chances:{{head:0.0f,chest:0.0f,"
            f"legs:0.0f,feet:0.0f,mainhand:0.0f,offhand:0.0f}}}}")


class Boss(Event):
    key = "boss"
    title = "БОС"
    duration = 600
    own_bar = True
    BAR = "bvg:boss"

    def __init__(self, engine, spec=None, target=None):
        super().__init__(engine)
        self.spec = boss_spec(spec or random.choice(BOSS_PRESETS))
        self.title = self.spec["name"]
        self.target = target
        self.phase = 1
        self.last_pos = None
        self.missing = 0
        self.next_taunt = time.time() + 40

    def start(self):
        ps = self.players()
        if self.target not in ps:
            self.target = random.choice(ps)
        sp = self.spec
        self.s.send(f"kill @e[tag=bvg_boss]")
        self.s.send(f"kill @e[tag=bvg_minion]")
        self.s.send(f"execute at {self.target} run summon {sp['mob']} "
                    f"~{random.choice([-9, 9])} ~1 ~{random.choice([-9, 9])} "
                    f"{boss_nbt(sp)}")
        self.s.send(f"bossbar remove {self.BAR}")
        self.s.send(f'bossbar add {self.BAR} {{"text":"{world.esc(sp["name"])}",'
                    f'"color":"{sp["color"]}","bold":true}}')
        self.s.send(f"bossbar set {self.BAR} color red")
        self.s.send(f"bossbar set {self.BAR} style notched_10")
        self.s.send(f"bossbar set {self.BAR} max {sp['hp']}")
        self.s.send(f"bossbar set {self.BAR} value {sp['hp']}")
        self.s.send(f"bossbar set {self.BAR} players @a")
        self.s.send(f"execute at {self.target} run playsound "
                    f"minecraft:entity.wither.spawn hostile @a ~ ~ ~ 0.8 0.8")
        world.announce(self.s, f"☠ {sp['name']}", "Бос се появи!", "dark_red")
        world.say(self.s, "keeper", f"Пазете се! {sp['name']} дойде за "
                                    f"{self.target}. Който го повали — слава!")
        if sp["taunts"]:
            world.speak(self.s, sp["name"], sp["color"], sp["taunts"][0])
        self._minions(1)

    def _minions(self, mult):
        mn = self.spec["minions"]
        if not mn:
            return
        for _ in range(mn["count"] * mult):
            self.s.send(f"execute at @e[tag=bvg_boss,limit=1] run summon "
                        f"{mn['mob']} ~{random.randint(-4, 4)} ~ "
                        f"~{random.randint(-4, 4)} "
                        f'{{Tags:["bvg_minion"],PersistenceRequired:1b}}')

    def tick(self):
        if self.done:
            return
        res = self.s.query_many([
            "data get entity @e[tag=bvg_boss,limit=1] Health",
            "data get entity @e[tag=bvg_boss,limit=1] Pos"])
        hp = None
        if res and res[0][0]:
            m = re.search(r"data:\s*([\d.]+)f", res[0][1] or "")
            hp = float(m.group(1)) if m else None
        if len(res) > 1 and res[1][0]:
            m = world.POS_RE.search(res[1][1] or "")
            if m:
                self.last_pos = tuple(float(v) for v in m.groups())
        if hp is None:
            self.missing += 1
            if self.missing >= 3:            # няма го, а не видяхме убиеца
                self.finish(self._nearest())
            return
        self.missing = 0
        self.s.send(f"bossbar set {self.BAR} value {int(hp)}", optional=True)
        frac = hp / max(1, self.spec["hp"])
        if self.phase == 1 and frac < 0.66:
            self.phase = 2
            world.announce(self.s, f"{self.spec['name']} побесня!",
                           "Втора фаза", "red")
            self.s.send("effect give @e[tag=bvg_boss] minecraft:speed 60 1 "
                        "true")
            self._minions(1)
        elif self.phase == 2 and frac < 0.33:
            self.phase = 3
            world.announce(self.s, f"{self.spec['name']} е на ръба!",
                           "Последна фаза — довършете го!", "gold")
            self.s.send("effect give @e[tag=bvg_boss] minecraft:strength 60 "
                        "1 true")
            self.s.send("effect give @e[tag=bvg_boss] minecraft:regeneration "
                        "8 1 true")
            self._minions(2)
        if time.time() > self.next_taunt and self.spec["taunts"]:
            self.next_taunt = time.time() + random.uniform(35, 60)
            world.speak(self.s, self.spec["name"], self.spec["color"],
                        random.choice(self.spec["taunts"]))
        self.s.send("execute at @e[tag=bvg_boss] run particle "
                    "minecraft:soul_fire_flame ~ ~1 ~ 0.6 1 0.6 0.02 12",
                    optional=True)
        if time.time() - self.started > self.duration:
            self.s.send("kill @e[tag=bvg_boss]")
            world.say(self.s, "keeper", f"{self.spec['name']} се оттегли в "
                                        f"мрака. Следващия път!")
            self.finish(None)

    def _nearest(self):
        best, bd = None, 40
        for p in self.players():
            d = world.distance(self.e.positions().get(p), self.last_pos)
            if d < bd:
                best, bd = p, d
        return best

    def on_event(self, ev):
        if ev.get("type") == "named_death" and \
                ev.get("message", "").startswith(self.spec["name"]):
            killer = ev.get("killer")
            self.finish(killer if killer in self.e.online()
                        else self._nearest())

    def finish(self, winner):
        if self.done:
            return
        self.done = True
        self.s.send(f"bossbar remove {self.BAR}")
        self.s.send("kill @e[tag=bvg_minion]")
        if not winner:
            self.s.send("kill @e[tag=bvg_boss]")
            return
        sp = self.spec
        world.reward(self.s, winner, sp["reward"], 120,
                     title=f"⚔ {sp['name']} падна!",
                     reason="Ти нанесе последния удар")
        world.announce(self.s, f"⚔ {winner} повали {sp['name']}!", "",
                       "gold")
        self.e.fame(winner, 60, f"повали {sp['name']}")
        for p in self.players():
            if p != winner and world.distance(self.e.positions().get(p),
                                              self.last_pos) < 48:
                give(self.s, p, "golden_apple 1")
                self.e.fame(p, 20, f"бой с {sp['name']}")
        self.e.won(winner, sp["name"])


class BloodMoon(Event):
    key = "bloodmoon"
    title = "КЪРВАВА ЛУНА"
    duration = 150

    def start(self):
        self.dead = set()
        self.s.send("time set midnight")
        self.s.send("weather thunder 160")
        world.announce(self.s, "🌑 КЪРВАВА ЛУНА", "Оцелейте до зазоряване",
                       "dark_red")
        world.say(self.s, "keeper", "Луната почервеня. Мъртвите се надигат "
                                    "по-често. Оцелелите ще бъдат наградени.")
        self.next_wave = time.time()

    def tick(self):
        if time.time() >= self.next_wave:
            self.next_wave = time.time() + 20
            for p in self.players():
                for _ in range(1 + self.e.cfg.get("chaos", 2) // 2):
                    mob = random.choice(["zombie", "skeleton", "husk",
                                         "spider", "stray"])
                    self.s.send(
                        f"execute at {p} run summon {mob} "
                        f"~{random.choice([-1, 1]) * random.randint(8, 14)} ~1 "
                        f"~{random.choice([-1, 1]) * random.randint(8, 14)} "
                        f'{{Tags:["bvg_blood"],PersistenceRequired:1b}}')
                self.s.send(f"execute at {p} run particle "
                            f"minecraft:dust{{color:[0.8,0.0,0.0],scale:2.0}} "
                            f"~ ~2 ~ 6 3 6 0 40", optional=True)
        super().tick()

    def on_event(self, ev):
        if ev["type"] == "death":
            self.dead.add(ev["player"])

    def finish(self, winner):
        self.done = True
        self.s.send("kill @e[tag=bvg_blood]")
        self.s.send("time set day")
        self.s.send("weather clear")
        alive = [p for p in self.players() if p not in self.dead]
        for p in alive:
            give(self.s, p, random.choice(REWARD_SMALL))
            self.e.fame(p, 15, "оцеля кървавата луна")
        world.say(self.s, "keeper", ("Слънцето изгря. Оцелели: " +
                                     ", ".join(alive)) if alive else
                  "Слънцето изгря над празно поле...")


class GoldRain(Event):
    key = "goldrain"
    title = "ЗЛАТЕН ДЪЖД"
    duration = 30

    def start(self):
        world.announce(self.s, "✨ ЗЛАТЕН ДЪЖД", "Събирайте!", "gold")
        world.say(self.s, "keeper", "Небето е щедро днес. Бързо!")

    def tick(self):
        for p in self.players():
            for _ in range(4):
                item = random.choices(["gold_nugget", "gold_ingot", "emerald",
                                       "diamond"], [12, 4, 3, 1])[0]
                self.s.send(f"execute at {p} run summon item "
                            f"~{random.randint(-6, 6)} ~{random.randint(8, 14)} "
                            f"~{random.randint(-6, 6)} "
                            f'{{Item:{{id:"minecraft:{item}",count:'
                            f"{random.randint(1, 3)}}}}}", optional=True)
            self.s.send(f"execute at {p} run particle minecraft:wax_on "
                        f"~ ~6 ~ 5 2 5 0 30", optional=True)
        super().tick()


class Invasion(Event):
    key = "invasion"
    title = "НАШЕСТВИЕ"
    duration = 300

    def start(self):
        self.where = None
        self.kills = 0
        ps = self.players()
        target = random.choice(ps)
        vpos = self.e.village_near(target) if hasattr(self.e, "village_near") \
            else None
        self.where = vpos
        anchor = (f"positioned {vpos[0]} {vpos[1]} {vpos[2]}" if vpos
                  else f"at {target}")
        n = 4 + 2 * int(self.e.cfg.get("chaos", 2))
        for i in range(n):
            mob = "vindicator" if i % 3 == 0 else "pillager"
            self.s.send(f"execute {anchor} run summon {mob} "
                        f"~{random.choice([-1, 1]) * random.randint(14, 20)} ~2 "
                        f"~{random.choice([-1, 1]) * random.randint(14, 20)} "
                        f'{{Tags:["bvg_raid"],PersistenceRequired:1b}}')
        world.announce(self.s, "⚔ НАШЕСТВИЕ", "Защитете селото!" if vpos
                       else f"Разбойници идват към {target}!", "red")
        world.say(self.s, "keeper", "Разбойници! Пазете селяните — "
                                    "всеки защитник ще бъде запомнен.")
        self.s.send("execute at @e[tag=bvg_raid,limit=1] run playsound "
                    "minecraft:event.raid.horn hostile @a ~ ~ ~ 2 1")

    def tick(self):
        ok, r = self.s.query("execute if entity @e[tag=bvg_raid]")
        if ok and "passed" not in (r or "").lower() and \
                time.time() - self.started > 8:
            self.finish(True)
            return
        super().tick()

    def finish(self, winner):
        if self.done:
            return
        self.done = True
        self.s.send("kill @e[tag=bvg_raid]")
        if winner:
            for p in self.players():
                give(self.s, p, random.choice(REWARD_SMALL))
                self.e.fame(p, 25, "отблъсна нашествието")
            world.announce(self.s, "🛡 Нашествието е отблъснато!", "",
                           "green")
            self.s.send("execute as @a at @s run playsound "
                        "minecraft:ui.toast.challenge_complete master @s")
        else:
            world.say(self.s, "keeper", "Разбойниците си тръгнаха с "
                                        "плячката. Срам!")


LIBRARY = {cls.key: cls for cls in (MeteorShower, Bounty, Treasure,
                                    NightOfTheDead, LowGravity, ChickenRain,
                                    Giant, Riddle, Race, Boss, BloodMoon,
                                    GoldRain, Invasion)}

# Колко „тежко" е всяко събитие — режисьорът го съобразява с хаоса
WEIGHT = {"chickens": 0, "gravity": 0, "riddle": 0, "goldrain": 0,
          "meteors": 1, "treasure": 1, "race": 1, "invasion": 2,
          "bounty": 2, "giant": 2, "boss": 2, "undead": 3, "bloodmoon": 3}


class Engine:
    """Пуска по едно събитие наведнъж и ги кара да вървят."""

    BAR = "bvg:event"

    def __init__(self, sender, cfg, online_fn, on_win=None, on_fame=None,
                 positions=None, village_near=None):
        self.sender = sender
        self.cfg = cfg
        self.online = online_fn
        self.on_win = on_win
        self.on_fame = on_fame
        self.positions = positions or (lambda: {})
        if village_near:
            self.village_near = village_near
        self.active = None
        self.history = []
        self.last_result = None
        self._bar = False

    def fame(self, player, pts, reason=""):
        if self.on_fame:
            try:
                self.on_fame(player, pts, reason)
            except Exception:
                pass

    def start_boss(self, spec=None, target=None):
        if self.active and not self.active.done:
            return False, f"вече върви {self.active.title}"
        ev = Boss(self, spec=spec, target=target)
        return self._run(ev, "boss")

    def won(self, player, title):
        self.last_result = (time.time(), player, title)
        if self.on_win:
            try:
                self.on_win(player, title)
            except Exception:
                pass

    def _bar_show(self, ev):
        self.sender.send(f"bossbar remove {self.BAR}")
        self.sender.send(f'bossbar add {self.BAR} {{"text":"'
                         f'{world.esc(ev.title)}","color":"gold"}}')
        self.sender.send(f"bossbar set {self.BAR} color yellow")
        self.sender.send(f"bossbar set {self.BAR} max {int(ev.duration)}")
        self.sender.send(f"bossbar set {self.BAR} value {int(ev.duration)}")
        self.sender.send(f"bossbar set {self.BAR} players @a")
        self._bar = True

    def _bar_hide(self):
        if self._bar:
            self.sender.send(f"bossbar remove {self.BAR}")
            self._bar = False

    def start_contest(self, preset=None, **kw):
        """Готово (preset) или измислено от режисьора състезание."""
        params = dict(CONTESTS.get(preset, {})) if preset else {}
        params.update({k: v for k, v in kw.items() if v is not None})
        if self.active and not self.active.done:
            return False, f"вече върви {self.active.title}"
        ev = Contest(self, preset=preset, **params)
        if not ev.valid():
            return False, "неразбираемо състезание"
        return self._run(ev, "contest")

    def _run(self, ev, key):
        try:
            self.active = ev
            ev.start()
        except Exception as e:
            self.active = None
            return False, f"грешка при старта ({type(e).__name__}: {e})"
        if ev.done:
            self.active = None
            return False, getattr(ev, "error", None) or "не тръгна"
        self.history.append((time.time(), key))
        if not getattr(ev, "own_bar", False):
            self._bar_show(ev)
        return True, ev.title

    def can_start(self, key):
        cls = LIBRARY.get(key)
        if not cls:
            return False, "няма такова събитие"
        if self.active and not self.active.done:
            return False, f"вече върви {self.active.title}"
        if len(self.online()) < cls.min_players:
            return False, f"иска поне {cls.min_players} играчи"
        return True, ""

    def start(self, key, **kw):
        ok, why = self.can_start(key)
        if not ok:
            return False, why
        try:
            ev = LIBRARY[key](self, **kw) if kw else LIBRARY[key](self)
        except Exception as e:
            return False, f"грешка при старта ({type(e).__name__}: {e})"
        return self._run(ev, key)

    def stop(self):
        if self.active and not self.active.done:
            try:
                self.active.finish(None)
            except Exception:
                self.active.done = True
        self.active = None
        self._bar_hide()

    def tick(self):
        a = self.active
        if not a:
            self._bar_hide()
            return
        if a.done:
            self.active = None
            self._bar_hide()
            return
        if not self.online():
            a.done = True            # всички излязоха — няма за кого
            self.active = None
            self._bar_hide()
            return
        if self._bar:
            left = max(0, int(a.duration - (time.time() - a.started)))
            self.sender.send(f"bossbar set {self.BAR} value {left}",
                             optional=True)
        try:
            a.tick()
        except Exception as e:
            # Едно счупено събитие не бива да спамва лога на всеки 2 сек
            a.done = True
            self.active = None
            self._bar_hide()
            raise RuntimeError(f"Събитието „{a.title}“ спря: "
                               f"{type(e).__name__}: {e}") from e

    def feed(self, ev):
        if self.active and not self.active.done:
            try:
                self.active.on_event(ev)
            except Exception:
                pass

    def pick(self, chaos):
        """Случайно събитие, съобразено с хаоса и броя играчи."""
        n = len(self.online())
        pool = [k for k, w in WEIGHT.items()
                if w <= chaos + 1 and LIBRARY[k].min_players <= n]
        recent = {k for _, k in self.history[-3:]}
        fresh = [k for k in pool if k not in recent] or pool
        return random.choice(fresh) if fresh else None

    def status(self):
        if self.active and not self.active.done:
            left = int(self.active.duration
                       - (time.time() - self.active.started))
            out = {"key": self.active.key, "title": self.active.title,
                   "left": max(0, left)}
            if isinstance(self.active, Contest):
                out["standings"] = self.active.standings()
                out["amount"] = self.active.amount
            return out
        return None
