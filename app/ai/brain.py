"""
brain.py — свързва събитията от сървъра с героите, режисьора и селата.

Решава кой отговаря в чата (дух, селянин или команда на админ), кога
режисьорът мисли, кога Шегаджията прави номер и как се коментира една
смърт. Ако AI-то не е достъпно (няма ключ или е изчерпан лимитът),
всичко продължава с готови реплики и сценарии — светът не замлъква.
"""

import json
import os
import random
import re
import threading
import time

from .. import paths
from ..logbus import log
from ..game import blueprints, events_lib, guide, world
from ..game.fame import RANKS, Fame, rank_of
from ..game.quests import Quests
from ..game.spirits import Visits
from ..game.villages import Villages
from . import characters, providers, safety
from .director import Director

PLAYERS_FILE = None          # тестовете могат да го сменят
NEAR_RADIUS = 7
PLAYER_COOLDOWN = 7
POS_EVERY = 5                # на колко секунди обновяваме позициите
SLOW_EVERY = 12              # селяните: къде са, нови села, задачи

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

IMPORTANT_ADV = ("diamond", "диамант", "nether", "нетер", "end", "края",
                 "dragon", "дракон", "wither", "elytra", "елитра", "beacon",
                 "маяк", "village", "село", "hero", "герой", "totem",
                 "тотем", "netherite", "нетерит", "trial", "ancient",
                 "monster hunter", "ловец", "sniper", "warden")

WORKER_PERSONA = """
Ти си {name} — селянин-строител в Minecraft сървъра BVG WORLD. Играчите
са българи. Говориш на български, кратко, като стар майстор от село:
топло, с поговорки и хумор. Живееш в село „{village}".

Можеш да строиш: къща (house), ферма (farm), кладенец (well), кула
(tower), градина (garden), фенер (lamp), сергия (stall). Стилове: oak
(дъб), spruce (смърч), birch (бреза), stone (камък), cherry (череша),
desert (пясък), mud (кал). Размери: small, medium, big.

Отговаряш САМО с JSON:
{{"say": "какво казваш", "job": {{"kind": "house", "style": "spruce",
"size": "medium"}}}}
„job" е само ако играчът наистина иска да построиш нещо; иначе null.
Ако вече строиш, кажи какво строиш и колко остава.
Сега: {now}.
"""


class Brain:
    def __init__(self, cfg, sender, server):
        self.cfg = cfg
        self.s = sender
        self.server = server
        self.engine = events_lib.Engine(sender, cfg,
                                        lambda: set(server.online),
                                        on_win=self._on_win,
                                        on_fame=self._on_fame,
                                        positions=self.positions,
                                        village_near=self.village_near)
        self.history = {k: [] for k in characters.CHARACTERS}
        self.worker_history = {}
        self.last_talk = {}
        self.players = self._load_players()
        self._stop = threading.Event()
        self._gen = 0
        self.next_prank = time.time() + 300
        self.busy_count = 0
        self.pos = {}                  # име -> (x, y, z), обновява се често
        self.dims = {}                 # име -> overworld / the_nether / ...
        self.pos_at = 0.0
        self.slow_at = 0.0
        self.villages = Villages(sender, cfg, self.positions,
                                 lambda: set(server.online))
        self.fame = Fame(sender, self)
        self.quests = Quests(sender, self)
        self.visits = Visits(self)
        self.director = Director(self)
        self.recent_deaths = {}        # име -> [времена]

    # ---------- данни за играчите ----------

    def _players_file(self):
        return PLAYERS_FILE or os.path.join(paths.DATA, "players.json")

    def _load_players(self):
        try:
            with open(self._players_file(), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_players(self):
        try:
            os.makedirs(os.path.dirname(self._players_file()), exist_ok=True)
            with open(self._players_file(), "w", encoding="utf-8") as f:
                json.dump(self.players, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def player(self, name):
        return self.players.setdefault(name, {"first": time.time(),
                                              "deaths": 0, "visits": 0})

    def _note_ip(self, name, ip):
        """Пази последните адреси на играча — само за собственика."""
        info = self.player(name)
        ips = [i for i in info.get("ips", []) if i != ip]
        info["ips"] = ([ip] + ips)[:5]
        info["last_ip"] = ip
        info["last_seen"] = time.time()
        self._save_players()

    def last_ip(self, name):
        return (self.players.get(name) or {}).get("last_ip")

    def is_admin(self, name):
        return name in (self.cfg.get("admins") or []) or \
            (name and name == self.cfg.get("owner_name"))

    def _on_win(self, player, title):
        info = self.player(player)
        info["wins"] = info.get("wins", 0) + 1
        self._save_players()
        self.fame.add(player, 30, f"победа: {title}")
        self.director.note(f"{player} спечели „{title}“", important=True)

    def _on_fame(self, player, pts, reason=""):
        self.fame.add(player, pts, reason)

    def village_near(self, player, radius=220):
        """Центърът на най-близкото село до играча (за нашествията)."""
        p = self.pos.get(player)
        best, bd = None, radius
        for v in self.villages.villages:
            c = v["center"]
            d = world.distance(p, c) if p else 1e9
            if d < bd:
                best, bd = tuple(c), d
        return best

    # ---------- позиции ----------

    def positions(self):
        return dict(self.pos)

    def cache_positions(self, info):
        if info:
            self.pos = {n: i["pos"] for n, i in info.items() if i.get("pos")}
            self.pos_at = time.time()

    def _refresh_positions(self):
        online = sorted(self.server.online)
        if not online:
            self.pos = {}
            return
        names = [n for n in online if world.name_ok(n)]
        cmds = []
        for n in names:
            cmds += [f"data get entity {n} Pos", f"data get entity {n} Dimension"]
        res = self.s.query_many(cmds)
        out, dims = {}, {}
        for i, n in enumerate(names):
            pair = res[2 * i:2 * i + 2]
            if len(pair) < 2:
                break
            (ok, r), (ok2, r2) = pair
            m = world.POS_RE.search(r or "") if ok else None
            if m:
                out[n] = tuple(float(v) for v in m.groups())
            m = world.STR_RE.search(r2 or "") if ok2 else None
            if m:
                dims[n] = m.group(1).replace("minecraft:", "")
        self.pos = out
        self.dims = dims
        self.pos_at = time.time()

    # ---------- цикъл ----------

    def start(self):
        # Нов „рунд" при всяко пускане: стар цикъл, който още спи, вижда,
        # че вече не е текущият, и спира — иначе събитията вървят двойно.
        self._gen += 1
        self._stop.clear()
        self.director.next_at = time.time() + 50
        self.visits.next_at = time.time() + 70
        self.next_prank = time.time() + 240
        threading.Thread(target=self._loop, args=(self._gen,),
                         daemon=True).start()

    def stop(self):
        self._stop.set()
        self.engine.stop()
        self.visits.active = None
        self.villages.save(force=True)
        self.quests.save()

    def _loop(self, gen):
        while not self._stop.is_set() and gen == self._gen:
            time.sleep(2)
            if not self.server.ready or gen != self._gen:
                continue
            for step in (self._tick_positions, self.engine.tick,
                         self._tick_villages, self._tick_quests,
                         self.visits.tick, self.director.tick,
                         self._trickster):
                try:
                    step()
                except Exception as e:
                    log.error("Мозък", f"{type(e).__name__}: {e}")

    def _tick_positions(self):
        if time.time() - self.pos_at > POS_EVERY:
            self._refresh_positions()

    def _tick_villages(self):
        self.villages.tick()
        if time.time() - self.slow_at < SLOW_EVERY or not self.server.online:
            return
        self.slow_at = time.time()
        self.villages.refresh_positions()
        self.villages.auto_found(self.dims)
        self.villages.offer_quests(self.quests, self.dims)

    def givers(self):
        out = self.villages.giver_positions()
        out.update(self.visits.giver_positions())
        return out

    def _tick_quests(self):
        if self.quests.items:
            self.quests.tick(self.positions(), self.givers())

    def _trickster(self):
        chaos = int(self.cfg.get("chaos") or 0)
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
            # остатъци от миналия път: гости, босове, ленти
            self.s.send_many(["kill @e[tag=bvg_visit]",
                              "kill @e[tag=bvg_boss]",
                              "kill @e[tag=bvg_minion]",
                              "bossbar remove bvg:boss",
                              "bossbar remove bvg:event"])
            self.fame.setup()
            self.respawn_npcs()
            self.villages.resume()
            log.ok("Мозък", "Светът е готов. Героите са на местата си.")
        elif t == "login":
            self._note_ip(ev["player"], ev["ip"])
        elif t == "join":
            self._on_join(ev["player"])
        elif t == "leave":
            self.director.note(f"{ev['player']} излезе")
        elif t == "death":
            self._on_death(ev)
        elif t == "chat":
            self._on_chat(ev["player"], ev["message"])
        elif t == "advancement":
            name = ev.get("name", "")
            big = any(w in name.lower() for w in IMPORTANT_ADV)
            self.director.note(f"{ev['player']} постигна „{name}“",
                               important=big)
            self.fame.add(ev["player"], 15 if big else 4, name[:30])
            if big or random.random() < 0.3:
                world.say(self.s, "keeper",
                          f"{ev['player']} постигна „{name}“. Браво!")
        elif t == "named_death":
            msg = ev.get("message", "")
            # „X was killed" е /kill — нашите духове и селяни, които
            # махаме и слагаме наново; не е новина за режисьора
            if not msg.endswith("was killed"):
                self.director.note(msg[:120], important=bool(ev.get("killer")))
        try:
            self.engine.feed(ev)
        except Exception:
            pass

    def _on_join(self, name):
        info = self.player(name)
        first = info["visits"] == 0
        away = time.time() - info.get("last_seen", time.time())
        info["visits"] += 1
        info["last_seen"] = time.time()
        self._save_players()
        self.fame.sync(name)
        self.director.note(
            f"{name} влезе" + (" за ПЪРВИ път" if first else
                               f" (не е идвал {int(away // 3600)} ч)"
                               if away > 6 * 3600 else ""),
            important=True)
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
            if self.is_admin(name):
                world.tell(self.s, name, "Ти си админ: напиши !помощ в чата "
                                         "за командите от играта.", "gold")
        threading.Thread(target=later, daemon=True).start()

    def _on_death(self, ev):
        name = ev["player"]
        info = self.player(name)
        info["deaths"] += 1
        self._save_players()
        now = time.time()
        recent = [t for t in self.recent_deaths.get(name, []) if now - t < 600]
        recent.append(now)
        self.recent_deaths[name] = recent
        self.director.note(f"{name} умря: {ev['message']}",
                           important=len(recent) >= 3)
        if not self.cfg.get("roast_deaths"):
            return
        if random.random() > 0.35 + 0.2 * int(self.cfg.get("chaos") or 0):
            return
        cause = ev["cause"]

        def work():
            reply = None
            if self._ai_ready():
                reply, _err = providers.ask(
                    self.cfg,
                    characters.system_prompt("trickster"),
                    [{"role": "user", "content":
                      f"{name} току-що умря: „{ev['message']}“. Това му е "
                      f"смърт номер {info['deaths']}. Кажи едно кратко "
                      f"смешно подигравателно изречение. Без команди."}],
                    role="fast", max_tokens=400, timeout=25)
            line = (reply or {}).get("say") if reply else None
            if not line:
                line = random.choice(ROASTS).format(p=name, cause=cause,
                                                    n=info["deaths"])
            world.say(self.s, "trickster", line)
        threading.Thread(target=work, daemon=True).start()

    # ---------- чат ----------

    def _on_chat(self, name, message):
        msg = message.strip()
        if msg.startswith("!"):
            threading.Thread(target=self._command, args=(name, msg),
                             daemon=True).start()
            return
        self.director.note(f"<{name}> {msg[:100]}")
        if not self.cfg.get("ai_enabled"):
            return
        # 1) дух, повикан по име  2) селянин по име  3) кой е наблизо
        key = characters.find_by_text(msg)
        worker = None if key else self.villages.find_worker(msg)
        if not key and not worker:
            pos = self.pos.get(name) or world.player_pos(self.s, name)
            key = self.visits.near(pos) or self._near_npc(name, pos)
            if not key and pos:
                worker = self.villages.worker_near(pos, 6)
        if not key and not worker:
            return
        if key and self.visits.active and self.visits.active["key"] == key:
            self.visits.stay(45)

        now = time.time()
        if now - self.last_talk.get(name, 0) < PLAYER_COOLDOWN \
                and not self.is_admin(name):
            world.tell(self.s, name, "Чакай малко, дай им да си поемат дъх.")
            return
        self.last_talk[name] = now
        if worker:
            threading.Thread(target=self.talk_worker,
                             args=(worker, name, msg), daemon=True).start()
        else:
            threading.Thread(target=self.talk, args=(key, name, msg),
                             daemon=True).start()

    def _near_npc(self, name, pos=None):
        pos = pos or world.player_pos(self.s, name)
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

    # ---------- команди от играта ----------

    HELP = ("!режисьор — AI решава какво да стане сега · "
            "!ai <задача> — свободна задача за режисьора · "
            "!събитие [име] · !състезание [име] · !бос [име] · !стоп · "
            "!гост [дух] — дух идва при теб · !село · !строй <къща|ферма|"
            "кула|кладенец|градина|фенер|сергия> [стил] · !дух <име> — "
            "постоянен дух тук · !слава <играч> <точки> · !сага — нова "
            "история · !ден · !нощ · !ясно · !op <играч>")
    PLAYER_HELP = ("!задачи — твоите задачи · !слава — рангът ти и "
                   "класацията · !история — какво става в света · "
                   "!летопис — книга с всичко досега. Говори с духовете и "
                   "селяните по име или застани до тях.")

    def tell_admin(self, name, text, col="gold"):
        if name:
            world.tell(self.s, name, text, col)
        else:
            log.info("Админ", text)

    def _command(self, name, msg):
        parts = msg[1:].strip().split(maxsplit=1)
        cmd = (parts[0] if parts else "").lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd in ("помощ", "help", "команди"):
            world.tell(self.s, name, self.PLAYER_HELP, "gold")
            if self.is_admin(name):
                self.tell_admin(name, "Админ: " + self.HELP)
            return
        if cmd in ("състояние", "status"):
            st = self.engine.status()
            world.tell(self.s, name, (f"{st['title']}: остават {st['left']} "
                                      f"сек") if st else "Нищо не върви.",
                       "gold")
            return
        if cmd in ("задачи", "задача", "мисии", "quests") and \
                not (arg and self.is_admin(name)):
            world.tell(self.s, name, "📜 " + self.quests.describe(name),
                       "gold")
            return
        if cmd in ("слава", "ранг", "класация", "fame") and \
                not (arg and self.is_admin(name)):
            pts = self.fame.points(name)
            idx = rank_of(pts)
            nxt = RANKS[idx + 1] if idx + 1 < len(RANKS) else None
            top = " · ".join(f"{i + 1}. {r['name']} ({r['fame']})"
                             for i, r in enumerate(self.fame.top(5)))
            world.tell(self.s, name,
                       f"Ти си {RANKS[idx][1]} — {pts} слава" +
                       (f", до {nxt[1]} остават {nxt[0] - pts}" if nxt else
                        "") + (f". Класация: {top}" if top else ""), "gold")
            return
        if cmd in ("история", "сага", "story") and \
                not (cmd == "сага" and self.is_admin(name)):
            st = self.director.story
            last = [t for _, t in st.get("chronicle", [])[-2:]]
            world.tell(self.s, name,
                       (f"„{st['title']}“, глава {st.get('chapter') or 1}. "
                        f"Цел: {st.get('goal') or '—'}. " if st.get("title")
                        else "Историята тепърва започва. ") +
                       " ".join(last), "gold")
            return
        if cmd in ("летопис", "книга", "chronicle"):
            self.s.send(self.director.book_command(name))
            world.tell(self.s, name, "Летописът е в инвентара ти.", "gold")
            return
        if not self.is_admin(name):
            world.tell(self.s, name, "Тази команда е само за админите.",
                       "red")
            return
        log.info("Админ", f"{name}: {msg[:80]}")
        try:
            self._admin(name, cmd, arg)
        except Exception as e:
            self.tell_admin(name, f"Грешка: {e}", "red")

    def _admin(self, name, cmd, arg):
        say = lambda t, c="gold": self.tell_admin(name, t, c)
        if cmd in ("режисьор", "gm", "изненада", "director"):
            if arg:
                ok, m = self.director.think("админ поиска", instruction=arg,
                                            admin=name)
            else:
                ok, m = self.director.think("админ поиска изненада")
            say(m if ok else f"Не сега: {m}")
        elif cmd in ("ai", "аи", "задача"):
            if not arg:
                return say("Напиши задачата: !ai направи арена тук")
            ok, m = self.director.think("админ поиска", instruction=arg,
                                        admin=name)
            say("Режисьорът се зае." if ok else f"Не сега: {m}")
        elif cmd in ("събитие", "event", "евент"):
            key = self._event_key(arg) if arg else \
                self.engine.pick(max(1, int(self.cfg.get("chaos") or 2)))
            if not key:
                return say("Не знам такова събитие. Има: " +
                           ", ".join(events_lib.LIBRARY))
            ok, m = self.start_event(key, source=name)
            say(f"Пуснах: {m}" if ok else f"Не тръгна: {m}",
                "gold" if ok else "red")
        elif cmd in ("състезание", "contest"):
            key = self._contest_key(arg) if arg else \
                random.choice(list(events_lib.CONTESTS))
            if not key:
                return say("Има: " + ", ".join(
                    f"{k} ({v})" for k, v in events_lib.CONTEST_NAMES.items()))
            ok, m = self.engine.start_contest(key)
            say(f"Пуснах: {m}" if ok else f"Не тръгна: {m}",
                "gold" if ok else "red")
        elif cmd in ("бос", "boss"):
            spec = self._boss_preset(arg)
            ok, m = self.engine.start_boss(spec, name)
            say(f"Пуснах боса: {m}" if ok else f"Не тръгна: {m}",
                "gold" if ok else "red")
        elif cmd in ("гост", "visit"):
            key = self._spirit_key(arg) if arg else None
            ok, m = self.visits.start(key, name, reason="админът го повика")
            say(m, "gold" if ok else "red")
        elif cmd in ("слава", "fame"):
            bits = arg.split()
            try:
                who, pts = bits[0], int(bits[1])
            except (IndexError, ValueError):
                return say("Напиши: !слава Ivan_99 50")
            if not world.name_ok(who):
                return say("Няма такъв играч.", "red")
            self.fame.add(who, max(-500, min(pts, 1000)), "от собственика")
            say(f"{who}: {self.fame.points(who)} слава.")
        elif cmd in ("задачи", "задача", "мисии", "quests"):
            from ..game.quests import VILLAGER_WANTS
            who = arg.split()[0] if arg else name
            k, t, n, r = random.choice(VILLAGER_WANTS["*"])
            ok, m = self.quests.give(who, k, t, n, reward=r, fame=15,
                                     text="Задача от собственика.")
            say(f"Задача за {who}: {m}" if ok else f"Не стана: {m}",
                "gold" if ok else "red")
        elif cmd in ("сага", "saga"):
            self.director.reset_story()
            ok, m = self.director.think("собственикът поиска нова сага")
            say("Започва нова история..." if ok else f"Не сега: {m}")
        elif cmd in ("стоп", "stop"):
            self.engine.stop()
            say("Спрях текущото събитие.")
        elif cmd in ("село", "village"):
            ok, m = self.villages.found_village(name)
            say(m, "gold" if ok else "red")
        elif cmd in ("строй", "построй", "build"):
            kind = blueprints.kind_from_text(arg) or "house"
            style = blueprints.style_from_text(arg)
            size = "big" if "голям" in arg else "small" if "малк" in arg \
                else None
            ok, m = self.villages.request_build(name, kind, style, size,
                                                owner=name)
            say(m, "gold" if ok else "red")
        elif cmd in ("дух", "spirit"):
            key = self._spirit_key(arg)
            if not key:
                return say("Кой дух? Пазителя, Майстора, Шегаджията или Мара.")
            ok, m = self.place_npc(key, name)
            say(m, "gold" if ok else "red")
        elif cmd in ("ден", "day"):
            self.s.send("time set day")
        elif cmd in ("нощ", "night"):
            self.s.send("time set night")
        elif cmd in ("ясно", "clear"):
            self.s.send("weather clear 1200")
        elif cmd == "op":
            who = arg.strip()
            if not world.name_ok(who):
                return say("Напиши името: !op Ivan_99")
            admins = list(self.cfg.get("admins") or [])
            if who not in admins:
                admins.append(who)
                self.cfg.set("admins", admins)
            self.server.command(f"op {who}")
            say(f"{who} вече е OP.")
        else:
            say("Не знам тази команда. Напиши !помощ")

    @staticmethod
    def _spirit_key(arg):
        low = (arg or "").lower().strip()
        if low in characters.CHARACTERS:
            return low
        return characters.find_by_text(arg) or next(
            (v for k, v in {"пазител": "keeper", "майстор": "builder",
                            "шегаджи": "trickster", "мар": "trader",
                            "търгов": "trader"}.items() if low.startswith(k)),
            None)

    @staticmethod
    def _boss_preset(arg):
        low = (arg or "").lower().strip()
        if not low:
            return None
        for p in events_lib.BOSS_PRESETS:
            if low[:4] in p["name"].lower():
                return p
        return {"name": arg.strip()[:28], "mob": "zombie", "scale": 2.4,
                "hp": 260, "damage": 8, "armor": ["netherite_helmet"],
                "weapon": "iron_sword", "minions": {"mob": "zombie",
                                                    "count": 3}}

    @staticmethod
    def _event_key(arg):
        low = arg.lower().strip()
        if low in events_lib.LIBRARY:
            return low
        names = {"метеор": "meteors", "лов": "bounty", "съкров": "treasure",
                 "мъртв": "undead", "зомби": "undead", "гравит": "gravity",
                 "пилет": "chickens", "кокош": "chickens", "гигант": "giant",
                 "гатанк": "riddle", "загадк": "riddle", "надбяг": "race",
                 "състез": "race", "кърв": "bloodmoon", "луна": "bloodmoon",
                 "злат": "goldrain", "дъжд": "goldrain", "нашеств": "invasion",
                 "разбой": "invasion", "бос": "boss"}
        return next((v for k, v in names.items() if k in low), None)

    @staticmethod
    def _contest_key(arg):
        low = arg.lower().strip()
        if low in events_lib.CONTESTS:
            return low
        for k, v in events_lib.CONTESTS.items():
            if v["title"].lower()[:5] in low or low[:5] in v["title"].lower():
                return k
        words = {"скок": "jumps", "диамант": "diamonds", "дърв": "wood",
                 "зомби": "zombies", "камък": "miner", "миньор": "miner",
                 "риб": "fishing", "жит": "farmer", "жътв": "farmer",
                 "живот": "breeder", "тича": "runner", "спринт": "runner"}
        return next((v for k, v in words.items() if k in low), None)

    # ---------- разговор ----------

    def _ai_ready(self):
        return bool(providers.configured(self.cfg))

    def _context(self, name, key):
        pos = self.pos.get(name) or world.player_pos(self.s, name)
        info = self.player(name)
        lines = [f"Говори ти играчът {name}."]
        if pos:
            lines.append(f"Той е на X={pos[0]:.0f} Y={pos[1]:.0f} "
                         f"Z={pos[2]:.0f}.")
        lines.append(f"Умирал е {info['deaths']} пъти, влизал е "
                     f"{info['visits']} пъти, победи {info.get('wins', 0)}.")
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
                ". Или състезание с \"contest\": едно от: " +
                ", ".join(events_lib.CONTESTS) +
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
                                   force=admin, role="fast", max_tokens=1500,
                                   timeout=40)
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
            va = self.visits.active
            if va and va["key"] == key:
                world.bubble_tag(self.s, va["tag"], text, 2.4)
                self.visits.stay(45)
            else:
                world.bubble(self.s, key, text)
                here = (self.cfg.get("characters") or {}).get(key) or {}
                if not (here.get("enabled", True) and here.get("pos")) and \
                        self.cfg.get("spirits_roam", True):
                    # викнат по име, а го няма наоколо — идва при играча
                    self.visits.start(key, name, line="", act=False,
                                      reason="повикаха го")

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

    def talk_worker(self, w, name, message):
        """Разговор със селянин — може да поеме строеж."""
        j = w.get("_job")
        now = (f"строиш {j.label}, готово {int(j.progress * 100)}%" if j
               else "свободен си")
        v = self.villages.village(w.get("village"))
        reply = None
        if self._ai_ready():
            hist = self.worker_history.get(w["id"], [])[-8:]
            system = WORKER_PERSONA.format(name=w["name"],
                                           village=v["name"] if v else "—",
                                           now=now)
            reply, err = providers.ask(
                self.cfg, system,
                hist + [{"role": "user", "content": f"{name}: {message}"}],
                force=self.is_admin(name), role="fast", max_tokens=900,
                timeout=35)
            if reply:
                self.worker_history[w["id"]] = (hist + [
                    {"role": "user", "content": f"{name}: {message}"},
                    {"role": "assistant",
                     "content": json.dumps(reply, ensure_ascii=False)[:400]}
                ])[-10:]
        job = (reply or {}).get("job") if reply else None
        say = str((reply or {}).get("say") or "").strip()
        if not reply:
            # без AI: разпознаваме молбата по думите
            kind = blueprints.kind_from_text(message)
            job = {"kind": kind, "style": blueprints.style_from_text(message)} \
                if kind else None
            say = ("Хайде, ще ти направя " +
                   blueprints.KIND_NAMES.get(kind, "нещо") + "." if kind
                   else (f"Сега {now}. Кажи ми какво да построя — къща, "
                         f"ферма, кула, кладенец, градина..."))
        pos = tuple(w.get("pos") or self.pos.get(name) or (0, 64, 0))
        if say:
            world.speak_near(self.s, w["name"], "yellow", say, pos)
            world.bubble_tag(self.s, f"bvg_w{w['id']}", say, 2.3)
        if isinstance(job, dict) and job.get("kind"):
            if j or w["id"] in self.villages.searching:
                return
            info = self.player(name)
            cool = float(self.cfg.get("build_cooldown") or 15) * 60
            if not self.is_admin(name) and \
                    time.time() - info.get("last_build", 0) < cool:
                world.speak_near(self.s, w["name"], "yellow",
                                 "Чакай, още не съм си починал от "
                                 "последния строеж за теб.", pos)
                return
            ok, msg = self.villages.request_build(
                name, job.get("kind"), job.get("style"), job.get("size"),
                owner=name, worker=w)
            if ok:
                info["last_build"] = time.time()
                self._save_players()
            else:
                world.speak_near(self.s, w["name"], "yellow", msg, pos)

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
        c = reply.get("contest")
        if isinstance(c, str) and c in events_lib.CONTESTS:
            self.engine.start_contest(c)

    # ---------- действия ----------

    def start_event(self, key, source="ръчно"):
        if key in events_lib.CONTESTS:
            ok, msg = self.engine.start_contest(key)
        else:
            ok, msg = self.engine.start(key)
        if ok:
            log.ok("Събитие", f"{msg} ({source})")
            self.director.note(f"започна {msg} ({source})")
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
