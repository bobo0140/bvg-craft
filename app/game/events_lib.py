"""
events_lib.py — събитията на сървъра.

Всяко събитие има начало, проверка на всеки няколко секунди, условие за
победа и край. Работят и без AI — тогава Пазителят ползва готовите си
реплики. С AI само говори по-шарено.
"""

import math
import random
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
        dist = random.randint(60, 130)
        self.x = int(pos[0] + math.cos(ang) * dist)
        self.z = int(pos[2] + math.sin(ang) * dist)
        self.last_hint = 0
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
            for r in REWARD_BIG:
                give(self.s, winner, r)
            world.announce(self.s, f"💎 {winner} намери съкровището!", "",
                           "aqua")
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
        self.s.send("time set midnight")
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


LIBRARY = {cls.key: cls for cls in (MeteorShower, Bounty, Treasure,
                                    NightOfTheDead, LowGravity, ChickenRain,
                                    Giant, Riddle, Race)}

# Колко „тежко" е всяко събитие — режисьорът го съобразява с хаоса
WEIGHT = {"chickens": 0, "gravity": 0, "riddle": 0, "meteors": 1,
          "treasure": 1, "race": 1, "bounty": 2, "giant": 2, "undead": 3}


class Engine:
    """Пуска по едно събитие наведнъж и ги кара да вървят."""

    def __init__(self, sender, cfg, online_fn):
        self.sender = sender
        self.cfg = cfg
        self.online = online_fn
        self.active = None
        self.history = []

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
        ev = LIBRARY[key](self, **kw) if kw else LIBRARY[key](self)
        self.active = ev
        ev.start()
        self.history.append((time.time(), key))
        return True, ev.title

    def stop(self):
        if self.active and not self.active.done:
            self.active.finish(None)
        self.active = None

    def tick(self):
        if self.active:
            if self.active.done:
                self.active = None
            else:
                self.active.tick()

    def feed(self, ev):
        if self.active and not self.active.done:
            self.active.on_event(ev)

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
            return {"key": self.active.key, "title": self.active.title,
                    "left": max(0, left)}
        return None
