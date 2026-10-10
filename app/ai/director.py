"""
director.py — режисьорът: изкуственият разум, който води играта сам.

Мисли постоянно — на около минута-две, и веднага, когато стане нещо
важно. Гледа света (кой къде е, живот, броня, инвентар, слава, задачи,
села, кой какво е постигнал, кой умря) и води ИСТОРИЯ: сага с глави и
цел, която продължава и между игрите. Във всяка глава решава какво да се
случи: събитие, бос с име и фази, състезание, задача, награда, гост от
духовете, ново село, постройка по свой чертеж, надпис, изненада.

Решенията идват като JSON с „действия". Всяко се превежда от нас в
правилни команди; суровите минават през safety.py. Без AI режисьорът
разказва готови саги — пак има история, босове и задачи.
"""

import json
import os
import random
import re
import threading
import time
from collections import deque

from .. import paths
from ..logbus import log
from ..game import events_lib, world
from ..game import blueprints as B
from . import providers, safety

MIN_GAP = 60             # сек между две бързи реакции
MAX_ACTIONS = 6
MAX_COMMANDS = 120
STORY_FILE = "story.json"

SYSTEM = """
Ти си РЕЖИСЬОРЪТ на Minecraft сървъра BVG WORLD — невидимият разказвач,
който прави този свят жив, изненадващ и незабравим. Играчите са българи,
приятели, които играят оцеляване. Пишеш на български.

ВОДИШ ИСТОРИЯ. Има сага (заглавие) с глави. Всяка глава има цел, която
играчите преследват (мисия, бос, загадка, строеж). Когато целта се
изпълни или мине време, започваш нова глава. Свързвай всичко с историята:
босът е злодеят на сагата, задачите водят към него, наградите имат смисъл.

ВСЕКИ ПЪТ правиш поне едно видимо нещо. На всеки 2–3 пъти — нещо
ГОЛЯМО (бос, събитие, състезание, нашествие, кървава луна, ново село,
постройка по твой чертеж, нова глава). Редувай — не повтаряй видовете от
последните си решения. Награждавай постиженията веднага. Помагай на
изоставащите, дразни на шега силните. Говори като Пазителя: театрално,
кратко, с хумор. Пиши имената на играчите.

Отговаряш САМО с JSON:
{"thought":"кратко защо","story":{"title":"...","chapter":2,"goal":"..."},
 "chronicle":"едно изречение за летописа","actions":[...]}
("story" и "chronicle" — само когато нещо се променя.)

ДЕЙСТВИЯ (полетата в скоби са по желание):
{"type":"announce","title":"до 30 знака","subtitle":"...","color":"gold","target":"@a или име"}
{"type":"chat","text":"реплика на Пазителя"}
{"type":"whisper","player":"име","text":"..."}
{"type":"event","key":"СЪБИТИЕ"}
{"type":"boss","near":"име","spec":{"name":"Грохот","mob":"zombie|husk|skeleton|stray|wither_skeleton|piglin_brute|vindicator|ravager|spider|blaze|witch|evoker","scale":2.5,"hp":300,"damage":9,"armor":["netherite_helmet"],"weapon":"diamond_axe","minions":{"mob":"zombie","count":3},"reward":["diamond 4"],"taunts":["..."]}}
{"type":"contest","preset":"СЪСТЕЗАНИЕ"}
{"type":"contest","kind":"score","criterion":"minecraft.mined:minecraft.coal_ore","amount":10,"minutes":6,"title":"...","desc":"...","reward":["emerald 5"]}
{"type":"quest","player":"име или all","kind":"collect|kill|mine|craft|visit","target":"wheat|zombie|coal_ore|...","amount":8,"title":"...","text":"защо","reward":["emerald 4"],"fame":20,"minutes":20,"at":[x,z]}
{"type":"reward","player":"име","items":["diamond 2"],"xp":50,"fame":20,"reason":"за какво"}
{"type":"fame","player":"име","points":30,"reason":"..."}
{"type":"spirit","key":"keeper|trickster|trader|builder","player":"име","text":"(по желание — какво да каже)"}
{"type":"effect","target":"име или @a","effect":"speed","seconds":30,"level":1}
{"type":"size","player":"име","scale":2.0,"seconds":30}
{"type":"spawn","mob":"zombie","near":"име","count":3,"name":"..."}
{"type":"weather","value":"clear|rain|thunder"}
{"type":"time","value":"day|noon|night|midnight"}
{"type":"prank","player":"име"}
{"type":"village","near":"име"}
{"type":"build","near":"име","kind":"house|farm|well|tower|garden|lamp|stall","style":"oak|spruce|birch|stone|cherry|desert|mud","size":"small|medium|big"}
{"type":"design","near":"име","name":"Арена на смелите","size":[11,6,11],"ops":[["fill",0,0,0,10,0,10,"stone_bricks"],["set",5,1,5,"lantern"]]}
{"type":"commands","list":["истински команди на Minecraft 1.21+, без /"]}

„design" е твоя постройка: x 0..ширина-1, y 0..височина-1 (0 = подът),
z 0..дълбочина-1; селянин ще я построи блок по блок на празно място до
играча. До 24×20×24, до 150 стъпки. Арени, паметници, статуи, порти,
кули, мостове, лабиринти — каквото подхожда на историята.

За „commands": координатите са истински — от снимката или „execute at
ИМЕ run ...". Не може: stop, op, whitelist, ban, kick, gamerule,
difficulty, creative, kill на играчи, wither, tnt, creeper.

Критерии за contest/quest: mined (блок), killed (моб), crafted, used,
picked_up, custom:jump|fish_caught|animals_bred|sprint_one_cm.
Предмети: истински имена (diamond, iron_ingot, oak_log, golden_apple...).
Награди: скромни, но приятни. Слава: 10–80 според подвига.
"""

# Готови саги — режисьорът ги разказва, когато няма AI
SAGAS = [
    {"title": "Проклятието на Грохот", "chapters": [
        {"goal": "Съберете желязо за оръжия", "do": [
            ("announce", "Глава 1: Тътенът", "Нещо се събужда под земята"),
            ("quest_all", "collect", "iron_ingot", 6, ["emerald 4"], 20)]},
        {"goal": "Оцелейте нощта на мъртвите", "do": [
            ("announce", "Глава 2: Нощта", "Мъртвите вървят пред него"),
            ("event", "undead")]},
        {"goal": "Повалете Грохот", "do": [
            ("announce", "Глава 3: Грохот", "Той дойде"),
            ("boss", 0)]},
        {"goal": "Почивка и награди", "do": [
            ("announce", "Краят на проклятието", "Светът диша отново"),
            ("event", "goldrain")]}]},
    {"title": "Паяжината на Кралицата", "chapters": [
        {"goal": "Избийте паяците", "do": [
            ("announce", "Глава 1: Нишките", "Паяците стават все повече"),
            ("quest_all", "kill", "spider", 3, ["emerald 4", "string 8"],
             20)]},
        {"goal": "Кървава луна", "do": [
            ("announce", "Глава 2: Червената нощ", "Пазете се"),
            ("event", "bloodmoon")]},
        {"goal": "Повалете Кралицата", "do": [
            ("announce", "Глава 3: Кралицата", "Тя е тук"),
            ("boss", 1)]}]},
    {"title": "Нашествието на Борил", "chapters": [
        {"goal": "Укрепете селото", "do": [
            ("announce", "Глава 1: Слухове", "Разбойници се събират"),
            ("quest_all", "collect", "cobblestone", 32,
             ["emerald 3", "iron_ingot 4"], 15)]},
        {"goal": "Отблъснете нашествието", "do": [
            ("announce", "Глава 2: Обсада", "Те идват"),
            ("event", "invasion")]},
        {"goal": "Победете Борил", "do": [
            ("announce", "Глава 3: Борил", "Водачът им лично"),
            ("boss", 3)]}]},
    {"title": "Златната треска", "chapters": [
        {"goal": "Намерете съкровището", "do": [
            ("announce", "Глава 1: Картата", "Някъде има злато"),
            ("event", "treasure")]},
        {"goal": "Най-добрият миньор", "do": [
            ("announce", "Глава 2: Рудниците", "Копайте!"),
            ("contest", "miner")]},
        {"goal": "Гнилия крал пази златото", "do": [
            ("announce", "Глава 3: Короната", "Кралят се надига"),
            ("boss", 4)]}]},
]

SMALL = ["contest", "goldrain", "spirit", "quest", "chickens", "riddle",
         "gravity", "meteors"]


class Director:
    def __init__(self, brain):
        self.b = brain
        self.notes = deque(maxlen=40)        # (време, текст)
        self.decisions = deque(maxlen=14)    # (време, кратко, източник, [...])
        self.next_at = time.time() + 90
        self.last_think = 0.0
        self.urgent = False
        self.busy = False
        self.reverts = []                    # (кога, [команди])
        self.last_error = ""
        self.last_source = ""
        self.ticks = 0
        self.kinds = deque(maxlen=12)        # видовете последни действия
        self.story = self._load_story()

    # ---------- история ----------

    @property
    def _story_path(self):
        return os.path.join(paths.DATA, STORY_FILE)

    def _load_story(self):
        try:
            with open(self._story_path, encoding="utf-8") as f:
                st = json.load(f)
            if isinstance(st, dict):
                st.setdefault("chronicle", [])
                return st
        except (OSError, ValueError):
            pass
        return {"title": "", "chapter": 0, "goal": "", "lore": [],
                "chronicle": [], "saga": -1, "step": 0, "updated": 0}

    def save_story(self):
        try:
            os.makedirs(paths.DATA, exist_ok=True)
            tmp = self._story_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.story, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._story_path)
        except OSError:
            pass

    def reset_story(self):
        self.story = {"title": "", "chapter": 0, "goal": "", "lore": [],
                      "chronicle": [], "saga": -1, "step": 0, "updated": 0}
        self.save_story()

    def chronicle(self, text):
        text = str(text or "").strip()[:200]
        if text:
            self.story["chronicle"].append([int(time.time()), text])
            self.story["chronicle"] = self.story["chronicle"][-80:]
            self.save_story()

    def _apply_story(self, st):
        if not isinstance(st, dict):
            return
        changed = False
        for k in ("title", "goal"):
            v = str(st.get(k) or "").strip()[:80]
            if v and v != self.story.get(k):
                self.story[k] = v
                changed = True
        try:
            ch = int(st.get("chapter") or 0)
        except (TypeError, ValueError):
            ch = 0
        if ch and ch != self.story.get("chapter"):
            self.story["chapter"] = ch
            changed = True
            self.chronicle(f"Глава {ch}: {self.story.get('goal', '')}")
        lore = str(st.get("lore") or "").strip()
        if lore:
            self.story.setdefault("lore", []).append(lore[:160])
            self.story["lore"] = self.story["lore"][-12:]
            changed = True
        if changed:
            self.story["updated"] = int(time.time())
            self.save_story()

    def book_command(self, player):
        from ..game import guide
        st = self.story
        pages = [f"§6§l{st.get('title') or 'Летопис'}§r\n\n"
                 f"Глава {st.get('chapter') or 1}\n\n§7Цел: "
                 f"{st.get('goal') or '—'}"]
        lines = [t for _, t in st.get("chronicle", [])[-40:]]
        page = ""
        for ln in lines:
            if len(page) + len(ln) > 230:
                pages.append(page)
                page = ""
            page += "• " + ln + "\n\n"
        if page:
            pages.append(page)
        return guide.book_command(player, "Летопис на BVG WORLD",
                                  "Режисьорът", pages[:30])

    # ---------- какво става ----------

    def note(self, text, important=False):
        self.notes.append((time.time(), str(text)[:160]))
        if important:
            self.urgent = True

    def schedule_revert(self, seconds, commands):
        self.reverts.append((time.time() + seconds, list(commands)))

    def period(self):
        chaos = int(self.b.cfg.get("chaos") or 0)
        base = int(self.b.cfg.get("gm_period") or 120)
        return max(45, base * {1: 2.0, 2: 1.0, 3: 0.6}.get(chaos, 1.0))

    def tick(self):
        now = time.time()
        due = [r for r in self.reverts if r[0] <= now]
        if due:
            self.reverts = [r for r in self.reverts if r[0] > now]
            for _, cmds in due:
                self.b.s.send_many(cmds)
        if self.busy or not self.b.server.online:
            return
        chaos = int(self.b.cfg.get("chaos") or 0)
        if chaos <= 0:
            return
        timer = now >= self.next_at
        quick = self.urgent and now - self.last_think > MIN_GAP
        if timer or quick:
            reason = "време е" if timer else "случи се нещо важно"
            self.urgent = False
            self.think(reason)

    def think(self, reason="", instruction=None, admin=None, wait=False):
        """Решава какво да стане. wait=True — в същата нишка (за тестове)."""
        if self.busy:
            return False, "Режисьорът вече мисли."
        self.busy = True
        self.last_think = time.time()
        if wait:
            return self._think(reason, instruction, admin)
        threading.Thread(target=self._think,
                         args=(reason, instruction, admin),
                         daemon=True).start()
        return True, "Режисьорът мисли..."

    def _ai_on(self):
        cfg = self.b.cfg
        return (cfg.get("gm_enabled", True) and cfg.get("ai_enabled", True)
                and bool(providers.configured(cfg)))

    def _think(self, reason, instruction, admin):
        try:
            self.ticks += 1
            plan, source = None, "сценарий"
            if self._ai_on():
                plan, err = self._ask(reason, instruction, admin)
                if plan is not None:
                    source = "AI"
                else:
                    self.last_error = err or ""
                    if err and err != "busy":
                        log.warn("Режисьор", f"AI не отговори ({err[:120]}). "
                                             f"Разказвам готова сага.")
            if plan is None:
                if instruction:
                    self.b.tell_admin(admin, "Без работещ AI не мога да "
                                             "изпълня свободна задача.")
                    return False, "Няма AI."
                plan = self.fallback()
            self._apply_story(plan.get("story"))
            done = self.execute(plan, admin=admin)
            if not done and not self.b.engine.active and not instruction:
                # никога не стоим без нищо — поне гост от духовете
                done = self.execute(self._small(), admin=None)
            if plan.get("chronicle"):
                self.chronicle(plan["chronicle"])
            self.next_at = time.time() + self.period() * \
                random.uniform(0.8, 1.2)
            summary = plan.get("thought") or ", ".join(done) or "нищо"
            self.decisions.append((time.time(), str(summary)[:160], source,
                                   done))
            self.last_source = source
            if done:
                log.ok("Режисьор", f"{', '.join(done)[:180]} ({source})")
            return True, ", ".join(done) or "реши да изчака"
        except Exception as e:
            log.error("Режисьор", f"{type(e).__name__}: {e}")
            self.next_at = time.time() + 120
            return False, str(e)
        finally:
            self.busy = False

    # ---------- снимка на света ----------

    def snapshot(self):
        b = self.b
        online = sorted(b.server.online)
        info = world.players_info(b.s, online) if online else {}
        b.cache_positions(info)
        daytime, day = world.time_of_day(b.s)
        lines = []
        st = self.story
        if st.get("title"):
            lines.append(f"САГА: „{st['title']}“, глава {st.get('chapter', 1)}"
                         f", цел: {st.get('goal') or '—'}")
            if st.get("lore"):
                lines.append("Знаем: " + " / ".join(st["lore"][-4:]))
        else:
            lines.append("САГА: още няма — измисли я сега (story).")
        if daytime is not None:
            phase = ("утро" if daytime < 3000 else "ден" if daytime < 9000
                     else "следобед" if daytime < 12000 else "залез"
                     if daytime < 13500 else "нощ" if daytime < 22500
                     else "зазоряване")
            lines.append(f"Време: {phase}, ден {day}.")
        lines.append(f"Онлайн ({len(online)}):")
        for n in online:
            i = info.get(n, {})
            pst = b.player(n)
            pos = i.get("pos")
            parts = [n]
            if pos:
                parts.append(f"X={pos[0]:.0f} Y={pos[1]:.0f} Z={pos[2]:.0f} "
                             f"{i.get('dim', '')}")
            if "health" in i:
                parts.append(f"♥{i['health']:.0f} 🍗{i.get('food', 20):.0f} "
                             f"ниво {i.get('level', 0):.0f}")
            parts.append("броня: " + (",".join(i.get("armor", [])[:4])
                                      or "няма"))
            if i.get("items"):
                parts.append("носи: " + ",".join(f"{k}×{v}" for k, v in
                                                 i["items"][:5]))
            parts.append(f"слава {pst.get('fame', 0)}, смърти "
                         f"{pst.get('deaths', 0)}, задачи "
                         f"{pst.get('quests', 0)}")
            if b.is_admin(n):
                parts.append("собственик")
            lines.append("- " + "; ".join(parts))
        ev = b.engine.status()
        lines.append("Сега върви: " + (f"{ev['title']} (остават "
                                       f"{ev['left']} сек)" if ev else "нищо"))
        if getattr(b, "quests", None):
            qs = b.quests.summary()
            if qs:
                lines.append("Задачи: " + "; ".join(qs[:6]))
        if b.villages:
            vs = b.villages.summary()
            if vs:
                lines.append("Села: " + "; ".join(vs[:6]))
        if self.notes:
            lines.append("Последно:")
            for t, text in list(self.notes)[-12:]:
                lines.append(f"- преди {int((time.time() - t) // 60)} мин: "
                             f"{text}")
        if self.decisions:
            lines.append("Твоите последни решения:")
            for t, text, _src, done in list(self.decisions)[-4:]:
                lines.append(f"- преди {int((time.time() - t) // 60)} мин: "
                             f"{', '.join(done) or text}")
        if self.kinds:
            lines.append("Последни видове действия (редувай!): " +
                         ", ".join(list(self.kinds)[-8:]))
        recent = [k for _, k in b.engine.history[-5:]]
        if recent:
            lines.append("Последни събития: " + ", ".join(recent))
        return "\n".join(lines), info

    def _ask(self, reason, instruction, admin):
        snap, _ = self.snapshot()
        chaos = int(self.b.cfg.get("chaos") or 2)
        mood = {1: "Спокойно: по-рядко големи неща.",
                2: "Весело: редовни изненади.",
                3: "ХАОС: често, смело, щуро — но честно."}.get(chaos, "")
        power = "пълен" if self.b.cfg.get("gm_power", "full") == "full" \
            else "ограничен (без команди върху играчите)"
        tools = ("СЪБИТИЯ: " + ", ".join(
            f"{k} ({c.title})" for k, c in events_lib.LIBRARY.items()
            if k != "boss") +
            "\nСЪСТЕЗАНИЯ: " + ", ".join(
                f"{k} ({v['title']})" for k, v in
                events_lib.CONTESTS.items()))
        user = (f"Защо гледаш сега: {reason or 'по график'}. Ход "
                f"{self.ticks}.\nНастроение: {mood} Достъп: {power}.\n"
                f"{tools}\n\nСНИМКА НА СВЕТА:\n{snap}")
        if instruction:
            user += (f"\n\nСОБСТВЕНИКЪТ ({admin or 'админ'}) ТИ НАРЕЖДА: "
                     f"{instruction}\nИзпълни го както най-добре можеш — с "
                     f"действия, design или команди. Обясни в чата.")
        reply, err = providers.ask(self.b.cfg, SYSTEM,
                                   [{"role": "user", "content": user}],
                                   force=True, timeout=75, max_tokens=5000,
                                   role="smart")
        if err:
            return None, err
        if not isinstance(reply, dict):
            return None, "Неразбираем отговор."
        if "actions" not in reply and "say" in reply:
            reply = {"actions": [{"type": "chat", "text": reply["say"]}]}
        return reply, None

    # ---------- без AI: готови саги ----------

    def _small(self):
        b = self.b
        online = sorted(b.server.online)
        if not online:
            return {"actions": []}
        kind = random.choice(SMALL)
        recent = {k for _, k in b.engine.history[-3:]}
        p = random.choice(online)
        if kind == "spirit" or b.engine.active:
            return {"actions": [{"type": "spirit", "player": p}]}
        if kind == "contest":
            free = [k for k in events_lib.CONTESTS if k not in recent]
            return {"actions": [{"type": "contest",
                                 "preset": random.choice(free or
                                                         list(events_lib.CONTESTS))}]}
        if kind == "quest":
            from ..game.quests import VILLAGER_WANTS
            k, t, n, r = random.choice(VILLAGER_WANTS["*"])
            return {"actions": [{"type": "quest", "player": p, "kind": k,
                                 "target": t, "amount": n, "reward": r,
                                 "fame": 15, "text": "Режисьорът има нужда "
                                                     "от теб."}]}
        return {"actions": [{"type": "event", "key": kind}]}

    def fallback(self):
        """Готови саги: глава на всеки ~3 хода, между тях — малки неща."""
        b = self.b
        st = self.story
        if b.engine.active:
            return {"actions": [{"type": "spirit",
                                 "player": random.choice(
                                     sorted(b.server.online))}],
                    "thought": "върви събитие — пращам гост"}
        if st.get("saga", -1) < 0 or st.get("saga", 0) >= len(SAGAS):
            st["saga"] = random.randrange(len(SAGAS))
            st["step"] = 0
            st["title"] = SAGAS[st["saga"]]["title"]
            st["chapter"] = 0
            self.chronicle(f"Започна сагата „{st['title']}“.")
        saga = SAGAS[st["saga"]]
        if self.ticks % 3 != 1:
            plan = self._small()
            plan["thought"] = "малка изненада между главите"
            return plan
        if st.get("step", 0) >= len(saga["chapters"]):
            st["saga"] = (st["saga"] + 1) % len(SAGAS)
            st["step"] = 0
            st["title"] = SAGAS[st["saga"]]["title"]
            st["chapter"] = 0
            self.chronicle(f"Започна сагата „{st['title']}“.")
            saga = SAGAS[st["saga"]]
        ch = saga["chapters"][st["step"]]
        st["step"] += 1
        st["chapter"] = st["step"]
        st["goal"] = ch["goal"]
        self.save_story()
        self.chronicle(f"„{saga['title']}“, глава {st['chapter']}: "
                       f"{ch['goal']}.")
        actions = []
        online = sorted(b.server.online)
        for step in ch["do"]:
            kind = step[0]
            if kind == "announce":
                actions.append({"type": "announce", "title": step[1],
                                "subtitle": step[2], "color": "gold"})
                actions.append({"type": "chat", "text":
                                f"{saga['title']} — {step[1]}. {step[2]}."})
            elif kind == "quest_all":
                for p in online:
                    actions.append({"type": "quest", "player": p,
                                    "kind": step[1], "target": step[2],
                                    "amount": step[3], "reward": step[4],
                                    "fame": step[5], "title": ch["goal"],
                                    "text": ch["goal"]})
            elif kind == "event":
                actions.append({"type": "event", "key": step[1]})
            elif kind == "contest":
                actions.append({"type": "contest", "preset": step[1]})
            elif kind == "boss":
                actions.append({"type": "boss", "spec":
                                events_lib.BOSS_PRESETS[step[1]],
                                "near": random.choice(online)})
        return {"actions": actions, "thought": f"{saga['title']}: "
                                               f"{ch['goal']}"}

    # ---------- изпълнение ----------

    def execute(self, plan, admin=None):
        b, s = self.b, self.b.s
        online = set(b.server.online)
        full = b.cfg.get("gm_power", "full") == "full" or bool(admin)
        done = []
        actions = plan.get("actions") or []
        if not isinstance(actions, list):
            return done
        sent = 0

        def who(name):
            return name if name in online else None

        def target(t):
            t = str(t or "@a").strip()
            return t if t in ("@a", "@p", "@r") or t in online else None

        for a in actions[:MAX_ACTIONS + 4]:
            if not isinstance(a, dict):
                continue
            t = str(a.get("type", "")).lower()
            n_before = len(done)
            try:
                if t == "announce":
                    tg = target(a.get("target"))
                    if tg:
                        world.announce(s, str(a.get("title", ""))[:40],
                                       str(a.get("subtitle", ""))[:70],
                                       a.get("color", "gold"), tg)
                        done.append(f"надпис „{str(a.get('title'))[:24]}“")
                elif t in ("chat", "say"):
                    text = str(a.get("text", ""))[:220]
                    if text:
                        world.say(s, "keeper", text)
                        done.append("реплика")
                elif t == "whisper":
                    p = who(a.get("player"))
                    if p:
                        world.whisper(s, p, "keeper",
                                      str(a.get("text", ""))[:200])
                        done.append(f"шепот на {p}")
                elif t == "actionbar":
                    tg = target(a.get("target"))
                    if tg:
                        world.actionbar(s, tg, str(a.get("text", ""))[:80])
                elif t == "event":
                    key = str(a.get("key", ""))
                    if key == "boss":
                        ok, msg = b.engine.start_boss(
                            a.get("spec"), who(a.get("near")))
                    else:
                        ok, msg = b.start_event(key, source="режисьорът")
                    if ok:
                        done.append(f"събитие {msg}")
                elif t == "boss":
                    ok, msg = b.engine.start_boss(a.get("spec"),
                                                  who(a.get("near")))
                    if ok:
                        done.append(f"бос {msg}")
                    else:
                        log.info("Режисьор", f"Босът не тръгна: {msg}")
                elif t == "contest":
                    preset = a.get("preset")
                    if preset in events_lib.CONTESTS:
                        ok, msg = b.engine.start_contest(preset)
                    else:
                        ok, msg = b.engine.start_contest(
                            kind=a.get("kind"), criterion=a.get("criterion"),
                            item=a.get("item"), amount=a.get("amount"),
                            minutes=a.get("minutes"), title=a.get("title"),
                            desc=a.get("desc"), xp=a.get("xp"),
                            reward=[r for r in (a.get("reward") or [])
                                    if world.item_spec(r)][:4] or None)
                    if ok:
                        done.append(f"състезание {msg}")
                    else:
                        log.info("Режисьор", f"Състезанието не тръгна: {msg}")
                elif t == "quest":
                    p = a.get("player")
                    players = sorted(online) if p in ("all", "@a", None, "") \
                        else ([p] if p in online else [])
                    giver = {"name": "Режисьорът", "color": "gold",
                             "kind": "gm"}
                    n = 0
                    for pl in players[:8]:
                        ok, _m = b.quests.give(
                            pl, a.get("kind"), a.get("target") or a.get("item"),
                            a.get("amount"), a.get("title"), a.get("text"),
                            a.get("reward"), a.get("xp"), a.get("fame", 15),
                            giver, a.get("minutes", 25), a.get("at"))
                        n += ok
                    if n:
                        done.append(f"задача за {n} играчи")
                elif t == "reward":
                    p = who(a.get("player"))
                    items = [i for i in (a.get("items") or [])
                             if world.item_spec(i)][:4]
                    items = [i for i in items
                             if world.item_spec(i)[0] not in safety.NEVER_ITEMS]
                    if p:
                        world.reward(s, p, items, min(int(a.get("xp") or 0),
                                                      300),
                                     title="🎁 Награда",
                                     reason=str(a.get("reason", ""))[:60])
                        if a.get("fame"):
                            b.fame.add(p, max(0, min(int(a["fame"]), 100)),
                                       str(a.get("reason", ""))[:40])
                        b.player(p)["gifts"] = b.player(p).get("gifts", 0) + 1
                        done.append(f"награда за {p}")
                elif t == "fame":
                    p = who(a.get("player"))
                    try:
                        pts = max(-50, min(int(a.get("points") or 0), 100))
                    except (TypeError, ValueError):
                        pts = 0
                    if p and pts:
                        b.fame.add(p, pts, str(a.get("reason", ""))[:40])
                        done.append(f"слава за {p}")
                elif t == "spirit":
                    key = a.get("key") or None
                    p = who(a.get("player"))
                    ok, msg = b.visits.start(key, p, line=a.get("text") or None,
                                             reason="режисьорът го прати")
                    if ok:
                        done.append(msg)
                elif t == "effect":
                    tg = target(a.get("target") or a.get("player"))
                    eff = re.sub(r"[^a-z_]", "", str(a.get("effect", "")))
                    secs = max(1, min(int(a.get("seconds") or 20), 120))
                    lvl = max(0, min(int(a.get("level") or 0), 3))
                    cmd = f"effect give {tg} minecraft:{eff} {secs} {lvl}"
                    ok, _ = safety.check([cmd], safety.FULL if full
                                         else ["effects"])
                    if tg and eff and ok:
                        s.send(ok[0])
                        done.append(f"ефект {eff}")
                elif t == "size":
                    p = who(a.get("player"))
                    try:
                        sc = max(0.3, min(float(a.get("scale") or 2), 3.5))
                    except (TypeError, ValueError):
                        sc = 2.0
                    secs = max(5, min(int(a.get("seconds") or 30), 120))
                    if p and full:
                        s.send(f"attribute {p} minecraft:scale base set {sc}")
                        self.schedule_revert(secs, [
                            f"attribute {p} minecraft:scale base reset"])
                        done.append(f"{p} стана {sc}×")
                elif t == "spawn":
                    p = who(a.get("near"))
                    mob = re.sub(r"[^a-z_]", "", str(a.get("mob", "")).replace(
                        "minecraft:", ""))
                    n = max(1, min(int(a.get("count") or 1), 8))
                    if p and mob and mob not in safety.NEVER_MOBS:
                        name = str(a.get("name") or "")[:24]
                        nbt = ('{Tags:["bvg_gm_mob"],PersistenceRequired:0b' +
                               (f',CustomName:{{text:"{world.esc(name)}",'
                                f'color:"red"}},CustomNameVisible:1b'
                                if name else "") + "}")
                        cmds = [f"execute at {p} run summon {mob} "
                                f"~{random.randint(-7, 7)} ~1 "
                                f"~{random.randint(-7, 7)} {nbt}"
                                for _ in range(n)]
                        ok, _ = safety.check(cmds, safety.FULL if full
                                             else ["effects", "mobs"])
                        s.send_many(ok)
                        if ok:
                            done.append(f"{len(ok)}× {mob} до {p}")
                elif t == "weather":
                    v = str(a.get("value", "clear"))
                    if v in ("clear", "rain", "thunder"):
                        s.send(f"weather {v} 600")
                        done.append(f"време {v}")
                elif t == "time":
                    # Имената вървят навсякъде; на 26.x числото нулира дните
                    v = str(a.get("value", ""))
                    v = {"sunset": "night"}.get(v, v)
                    if v in ("day", "noon", "night", "midnight"):
                        s.send(f"time set {v}")
                        done.append(f"време {v}")
                elif t == "prank":
                    p = who(a.get("player"))
                    if p:
                        b.prank(p)
                        done.append(f"номер на {p}")
                elif t == "village":
                    p = who(a.get("near")) or (sorted(online)[0]
                                               if online else None)
                    if p and b.villages:
                        ok, msg = b.villages.found_village(p)
                        if ok:
                            done.append(f"ново село до {p}")
                elif t == "build":
                    p = who(a.get("near"))
                    if p and b.villages:
                        ok, msg = b.villages.request_build(
                            p, a.get("kind") or "house", a.get("style"),
                            a.get("size"), owner="режисьорът")
                        if ok:
                            done.append(f"строеж до {p}")
                elif t == "design":
                    p = who(a.get("near")) or (sorted(online)[0]
                                               if online else None)
                    custom, why = design_plan(a)
                    if p and custom and b.villages:
                        ok, msg = b.villages.request_plan(p, custom,
                                                          owner="режисьорът")
                        if ok:
                            done.append(f"постройка „{custom['label']}“")
                    elif why:
                        log.info("Режисьор", f"Чертежът не става: {why}")
                elif t == "story":
                    self._apply_story(a)
                elif t == "chronicle":
                    self.chronicle(a.get("text"))
                elif t == "commands":
                    cmds = a.get("list") or a.get("commands") or []
                    if isinstance(cmds, str):
                        cmds = [cmds]
                    powers = safety.FULL if full else \
                        ["effects", "world", "score", "mobs", "move"]
                    ok, refused = safety.check(
                        cmds, powers, limit=MAX_COMMANDS - sent,
                        log=lambda m: log.warn("Режисьор", m))
                    s.send_many(ok)
                    sent += len(ok)
                    if ok:
                        done.append(f"{len(ok)} команди")
            except Exception as e:
                log.warn("Режисьор", f"Действие {t}: {type(e).__name__}: {e}")
            if len(done) > n_before:
                self.kinds.append(t)
        return done


# ---------------------------------------------------------------------------
# Чертежи от AI
# ---------------------------------------------------------------------------

DESIGN_NEVER = {"tnt", "lava", "fire", "soul_fire", "water", "bedrock",
                "barrier", "spawner", "trial_spawner", "command_block",
                "chain_command_block", "repeating_command_block",
                "structure_block", "jigsaw", "end_portal", "end_gateway",
                "nether_portal", "respawn_anchor", "magma_block",
                "powder_snow", "cobweb", "sculk_shrieker", "light",
                "structure_void", "test_block", "test_instance_block"}


def design_plan(a):
    """Проверява чертеж от AI. -> (custom за Job, или None, защо)."""
    try:
        w, h, d = (int(v) for v in (a.get("size") or [])[:3])
    except (TypeError, ValueError):
        return None, "няма размер"
    if not (2 <= w <= 24 and 2 <= h <= 20 and 2 <= d <= 24):
        return None, "размерът е извън 24×20×24"
    ops = []
    for raw in (a.get("ops") or [])[:150]:
        if not isinstance(raw, (list, tuple)) or not raw:
            continue
        kind = str(raw[0]).lower()
        try:
            if kind == "fill" and len(raw) >= 8:
                c = [int(v) for v in raw[1:7]]
                block = str(raw[7])
            elif kind in ("set", "setblock") and len(raw) >= 5:
                c = [int(v) for v in raw[1:4]] * 2
                block = str(raw[4])
            else:
                continue
        except (TypeError, ValueError):
            continue
        block = block.strip().lower().replace("minecraft:", "")
        if not B.BLOCK_RE.match(block) or block.startswith("#"):
            continue
        if block.split("[")[0] in DESIGN_NEVER:
            continue
        xs, ys, zs = (c[0], c[3]), (c[1], c[4]), (c[2], c[5])
        if min(xs) < 0 or max(xs) >= w or min(ys) < 0 or max(ys) >= h or \
                min(zs) < 0 or max(zs) >= d:
            continue
        if kind == "fill":
            ops.append(["F", c[0], c[1], c[2], c[3], c[4], c[5], block])
        else:
            ops.append(["S", c[0], c[1], c[2], block])
    if len(ops) < 3:
        return None, "твърде малко стъпки"
    label = str(a.get("name") or "постройка")[:40]
    return {"w": w, "d": d, "h": h, "ops": ops, "label": label,
            "door": [w // 2, d]}, None
