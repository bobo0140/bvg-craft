"""
brain.py — свързва събитията от сървъра с героите.

Решава кой герой отговаря, кога Пазителят пуска събитие, кога
Шегаджията прави номер и как се коментира една смърт. Ако AI-то не е
достъпно (няма ключ или е изчерпан лимитът), героите продължават да
говорят с готови реплики — светът не замлъква.
"""

import json
import os
import random
import threading
import time

from .. import paths
from ..logbus import log
from ..game import events_lib, guide, world
from . import characters, providers, safety

PLAYERS_FILE = os.path.join(paths.DATA, "players.json")
NEAR_RADIUS = 7
PLAYER_COOLDOWN = 7

ROASTS = [
    "{p} {cause}. Аплодисменти, моля.",
    "{p} отново доказа, че гравитацията работи.",
    "Почивай в мир, {p}. Или поне докато се респаунеш.",
    "{p}, това беше... интересен избор.",
    "Някой да запише това. {p} {cause}.",
    "{p} {cause}. Шегаджията вече се смее.",
    "Смърт номер {n} за {p}. Съхранявам статистиката.",
]
GREETS = [
    "Добре дошъл, {p}! Светът те чакаше.",
    "{p} се появи. Пазете съкровищата си.",
    "Ааа, {p}! Отдавна не сме се виждали.",
]
FIRST_GREET = ("Добре дошъл в BVG WORLD, {p}! Дадох ти книга — "
               "прочети я, за да знаеш кой живее тук.")
PRANKS = [
    ["execute at {p} run summon chicken ~ ~3 ~",
     "execute at {p} run summon chicken ~1 ~4 ~",
     "execute at {p} run summon chicken ~-1 ~5 ~"],
    ["effect give {p} minecraft:levitation 3 1 true"],
    ["effect give {p} minecraft:jump_boost 15 6 true"],
    ["execute at {p} run summon pig ~ ~2 ~ {{CustomName:{{text:\"Шегаджията "
     "те поздравява\"}},CustomNameVisible:1b}}"],
    ["effect give {p} minecraft:invisibility 10 0 true"],
    ["execute at {p} run playsound minecraft:entity.creeper.primed master "
     "{p} ~ ~ ~ 1 1"],
    ["effect give {p} minecraft:speed 10 5 true"],
]
PRANK_LINES = ["Хехе.", "Изненада!", "Не беше аз. Или беше?",
               "Това е за твое добро.", "ХАХАХА!"]


class Brain:
    def __init__(self, cfg, sender, server):
        self.cfg = cfg
        self.s = sender
        self.server = server
        self.engine = events_lib.Engine(sender, cfg,
                                        lambda: set(server.online))
        self.history = {k: [] for k in characters.CHARACTERS}
        self.last_talk = {}
        self.players = self._load_players()
        self._stop = threading.Event()
        self.next_event = time.time() + 120
        self.next_prank = time.time() + 300
        self.busy_count = 0

    # ---------- данни за играчите ----------

    def _load_players(self):
        try:
            with open(PLAYERS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_players(self):
        try:
            with open(PLAYERS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.players, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def player(self, name):
        return self.players.setdefault(name, {"first": time.time(),
                                              "deaths": 0, "visits": 0})

    def is_admin(self, name):
        return name in (self.cfg.get("admins") or [])

    # ---------- цикъл ----------

    def start(self):
        self._stop.clear()
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()
        self.engine.stop()

    def _loop(self):
        while not self._stop.is_set():
            time.sleep(2)
            if not self.server.ready:
                continue
            try:
                self.engine.tick()
                self._director()
                self._trickster()
            except Exception as e:
                log.error("Мозък", f"{type(e).__name__}: {e}")

    def _director(self):
        if not self.cfg.get("ai_enabled") or self.cfg.get("chaos") == 0:
            return
        if not self.server.online or self.engine.active:
            return
        if time.time() < self.next_event:
            return
        mins = max(2, int(self.cfg.get("director_minutes")))
        jitter = random.uniform(0.7, 1.3)
        self.next_event = time.time() + mins * 60 * jitter
        key = self.engine.pick(self.cfg.get("chaos"))
        if key:
            self.start_event(key, source="режисьорът")

    def _trickster(self):
        chaos = self.cfg.get("chaos")
        if chaos < 2 or not self.server.online:
            return
        if time.time() < self.next_prank:
            return
        self.next_prank = time.time() + random.uniform(240, 600) / chaos
        victim = random.choice(sorted(self.server.online))
        self.prank(victim)

    # ---------- събития от сървъра ----------

    def on_event(self, ev):
        t = ev["type"]
        if t == "ready":
            self.respawn_npcs()
            log.ok("Мозък", "Светът е готов. Героите са на местата си.")
        elif t == "join":
            self._on_join(ev["player"])
        elif t == "death":
            self._on_death(ev)
        elif t == "chat":
            self._on_chat(ev["player"], ev["message"])
        elif t == "advancement":
            if random.random() < 0.3:
                world.say(self.s, "keeper",
                          f"{ev['player']} постигна „{ev['name']}“. Браво!")
        self.engine.feed(ev)

    def _on_join(self, name):
        info = self.player(name)
        first = info["visits"] == 0
        info["visits"] += 1
        self._save_players()
        if not self.cfg.get("greet_joins"):
            return

        def later():
            time.sleep(3)          # да се зареди светът при играча
            if first:
                self.s.send(guide.give_command(name))
                world.say(self.s, "keeper", FIRST_GREET.format(p=name))
                world.sound(self.s, name,
                            "minecraft:ui.toast.challenge_complete")
            else:
                world.say(self.s, "keeper",
                          random.choice(GREETS).format(p=name))
        threading.Thread(target=later, daemon=True).start()

    def _on_death(self, ev):
        name = ev["player"]
        info = self.player(name)
        info["deaths"] += 1
        self._save_players()
        if not self.cfg.get("roast_deaths"):
            return
        if random.random() > 0.35 + 0.2 * self.cfg.get("chaos"):
            return
        cause = ev["cause"]

        def work():
            reply, err = None, None
            if self._ai_ready():
                reply, err = providers.ask(
                    self.cfg,
                    characters.system_prompt("trickster"),
                    [{"role": "user", "content":
                      f"{name} току-що умря: „{ev['message']}“. Това му е "
                      f"смърт номер {info['deaths']}. Кажи едно кратко "
                      f"смешно подигравателно изречение. Без команди."}])
            line = (reply or {}).get("say") if reply else None
            if not line:
                line = random.choice(ROASTS).format(p=name, cause=cause,
                                                    n=info["deaths"])
            world.say(self.s, "trickster", line)
        threading.Thread(target=work, daemon=True).start()

    def _on_chat(self, name, message):
        if not self.cfg.get("ai_enabled"):
            return
        key = characters.find_by_text(message)
        if not key:
            key = self._near_npc(name)
        if not key:
            return

        now = time.time()
        if now - self.last_talk.get(name, 0) < PLAYER_COOLDOWN \
                and not self.is_admin(name):
            world.whisper(self.s, name, key, "Чакай малко, дай ми да "
                                             "си поема дъх.")
            return
        self.last_talk[name] = now
        threading.Thread(target=self.talk, args=(key, name, message),
                         daemon=True).start()

    def _near_npc(self, name):
        pos = world.player_pos(self.s, name)
        if not pos:
            return None
        best, best_d = None, NEAR_RADIUS
        for key, data in (self.cfg.get("characters") or {}).items():
            p = data.get("pos")
            if data.get("enabled", True) and p:
                d = world.distance(pos, p)
                if d < best_d:
                    best, best_d = key, d
        return best

    # ---------- разговор ----------

    def _ai_ready(self):
        prov = self.cfg.get("ai_provider")
        return bool(self.cfg.get("gemini_key" if prov == "gemini"
                                 else "openai_key"))

    def _context(self, name, key):
        pos = world.player_pos(self.s, name)
        info = self.player(name)
        lines = [f"Говори ти играчът {name}."]
        if pos:
            lines.append(f"Той е на X={pos[0]:.0f} Y={pos[1]:.0f} "
                         f"Z={pos[2]:.0f}.")
        lines.append(f"Умирал е {info['deaths']} пъти, влизал е "
                     f"{info['visits']} пъти.")
        if self.is_admin(name):
            lines.append("Той е собственикът на сървъра — слушай го.")
        others = sorted(self.server.online - {name})
        if others:
            lines.append("Други онлайн: " + ", ".join(others[:12]) + ".")
        st = self.engine.status()
        if st:
            lines.append(f"В момента върви събитие: {st['title']}.")
        if key == "keeper":
            lines.append(
                "Можеш да пуснеш събитие, като добавиш в JSON-а поле "
                "\"event\" с едно от: " + ", ".join(events_lib.LIBRARY) +
                ". За гатанка можеш да дадеш и \"riddle\": "
                "{\"q\": \"въпрос\", \"a\": [\"отговор\", \"вариант\"]}.")
        return "\n".join(lines)

    def talk(self, key, name, message, audio=None):
        ch = characters.CHARACTERS[key]
        if not self._ai_ready():
            world.whisper(self.s, name, key,
                          "Днес съм мълчалив. (Няма свързан AI ключ.)")
            return
        admin = self.is_admin(name)
        hist = self.history[key][-10:]
        msgs = hist + [{"role": "user",
                        "content": f"{name}: {message or '(гласово)'}"}]
        system = characters.system_prompt(key, self._context(name, key))
        reply, err = providers.ask(self.cfg, system, msgs, audio=audio,
                                   force=admin)
        if err == "busy":
            self.busy_count += 1
            world.whisper(self.s, name, key, "Много хора ме питат наведнъж. "
                                             "Опитай след малко.")
            return
        if err:
            log.warn(ch["name"], err)
            world.whisper(self.s, name, key, "Нещо ми се замая главата. "
                                             "Пробвай пак.")
            return

        text = str(reply.get("say") or "").strip()
        self.history[key] = (hist + [
            {"role": "user", "content": f"{name}: {message}"},
            {"role": "assistant", "content": json.dumps(
                reply, ensure_ascii=False)[:600]}])[-12:]

        if text:
            world.say(self.s, key, text)
            world.bubble(self.s, key, text)

        powers = list(ch["powers"])
        limit = 80 if admin else 30
        ok, refused = safety.check(reply.get("commands"), powers,
                                   log=lambda m: log.warn(ch["name"], m),
                                   limit=limit)
        self.s.send_many(ok)
        if ok:
            log.info(ch["name"], f"{name}: {message[:50]} → {len(ok)} команди")

        if key == "keeper":
            self._keeper_extras(reply)

    def _keeper_extras(self, reply):
        riddle = reply.get("riddle")
        if isinstance(riddle, dict) and riddle.get("q") and riddle.get("a"):
            answers = riddle["a"] if isinstance(riddle["a"], list) \
                else [riddle["a"]]
            self.engine.start("riddle", question=riddle["q"],
                              answers=[str(a) for a in answers])
            return
        ev = reply.get("event")
        if isinstance(ev, str) and ev in events_lib.LIBRARY:
            self.start_event(ev, source="Пазителят")

    # ---------- действия ----------

    def start_event(self, key, source="ръчно"):
        ok, msg = self.engine.start(key)
        if ok:
            log.ok("Събитие", f"{msg} ({source})")
        else:
            log.warn("Събитие", f"Не тръгна: {msg}")
        return ok, msg

    def prank(self, victim):
        cmds = [c.format(p=victim) for c in random.choice(PRANKS)]
        ok, _ = safety.check(cmds, characters.CHARACTERS["trickster"]
                             ["powers"])
        self.s.send_many(ok)
        world.say(self.s, "trickster", random.choice(PRANK_LINES))
        log.info("Шегаджията", f"Номер на {victim}")

    def respawn_npcs(self):
        for key, data in (self.cfg.get("characters") or {}).items():
            if data.get("enabled", True) and data.get("pos"):
                world.spawn_npc(self.s, key, data["pos"],
                                data.get("yaw", 0.0))

    def place_npc(self, key, near_player):
        pos = world.player_pos(self.s, near_player)
        if not pos:
            return False, "Не намирам играча в света."
        import math
        yaw = world.player_yaw(self.s, near_player)
        # два блока пред играча, обърнат към него
        rad = math.radians(yaw)
        front = (pos[0] - math.sin(rad) * 2, pos[1], pos[2] + math.cos(rad) * 2)
        chars = dict(self.cfg.get("characters") or {})
        chars[key] = {"enabled": True, "pos": list(front),
                      "yaw": (yaw + 180 + 180) % 360 - 180}
        self.cfg.set("characters", chars)
        world.spawn_npc(self.s, key, front, chars[key]["yaw"])
        return True, f"{characters.CHARACTERS[key]['name']} застана тук."

    def remove_npc(self, key):
        chars = dict(self.cfg.get("characters") or {})
        if key in chars:
            chars[key]["enabled"] = False
        self.cfg.set("characters", chars)
        world.despawn_npc(self.s, key)
