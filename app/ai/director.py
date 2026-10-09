"""
director.py — режисьорът: изкуственият разум, който води играта.

На всеки няколко минути (и когато се случи нещо важно) режисьорът
поглежда света: кой е онлайн, къде е, колко живот има, какво носи,
кой какво е постигнал, кой умря и от какво, какво си говорят. После
решава какво да се случи: събитие, състезание, награда за някого,
изненада, ново село, номер, надпис — или нищо, ако е твърде скоро.

Решенията идват като JSON с „действия". Всяко действие се превежда от
нас в правилни команди, а суровите команди минават през safety.py. Без
AI ключ режисьорът продължава да работи с готови сценарии.
"""

import json
import random
import re
import threading
import time
from collections import deque

from ..logbus import log
from ..game import events_lib, world
from ..game import blueprints as B
from . import characters, providers, safety

MIN_GAP = 75             # секунди между две решения на AI
MAX_ACTIONS = 8
MAX_COMMANDS = 120

SYSTEM = """
Ти си РЕЖИСЬОРЪТ на Minecraft сървъра BVG WORLD — невидимият разказвач,
който прави играта жива, разнообразна и интересна. Играчите са българи,
приятели, които играят заедно оцеляване. Говориш на български.

Какво правиш:
- Гледаш какво става и решаваш какво да се случи СЕГА. Понякога голямо
  събитие, понякога малка изненада, понякога само похвала. Не бъди
  еднообразен — редувай видовете действия и не повтаряй последните си
  решения.
- Награждаваш постижения (първи диамант, Нетер, победа) и смели неща.
  Наградите са скромни, но приятни: няколко диаманта, злато, храна,
  опит, понякога нещо рядко.
- Помагаш на изостаналите (много смърти, гол без броня нощем), дразниш
  на шега силните. Шегите са кратки и безобидни.
- Пускаш състезания с ясна цел и награда; събития от списъка; строиш
  села със селяни близо до играчите (те растат сами).
- Говориш като Пазителя — театрално, с хумор, кратко.
- Ако току-що е имало събитие или играчите са заети, избери нещо малко
  или нищо.

Отговаряш САМО с JSON:
{"thought": "кратко защо", "actions": [ ... ], "next_in_minutes": 6}

Видове действия (полета в скоби са по желание):
{"type":"announce","title":"до 30 знака","subtitle":"...","color":"gold","target":"@a или име"}
{"type":"chat","text":"реплика на Пазителя в чата"}
{"type":"whisper","player":"име","text":"лично съобщение"}
{"type":"actionbar","target":"@a или име","text":"..."}
{"type":"event","key":"едно от СЪБИТИЯ"}
{"type":"contest","preset":"едно от СЪСТЕЗАНИЯ"}
{"type":"contest","kind":"score","criterion":"minecraft.mined:minecraft.coal_ore","amount":10,"minutes":6,"title":"...","desc":"...","reward":["diamond 2"]}
{"type":"contest","kind":"items","item":"minecraft:iron_ingot","amount":8,"minutes":8,"title":"...","desc":"...","reward":["emerald 5"]}
{"type":"reward","player":"име","items":["diamond 2","golden_apple 1"],"xp":50,"reason":"за какво"}
{"type":"effect","target":"име или @a","effect":"speed","seconds":30,"level":1}
{"type":"size","player":"име","scale":2.0,"seconds":30}
{"type":"spawn","mob":"zombie","near":"име","count":3,"name":"(име на моба)"}
{"type":"weather","value":"clear|rain|thunder"}
{"type":"time","value":"day|noon|night|midnight"}
{"type":"prank","player":"име"}
{"type":"village","near":"име"}
{"type":"build","near":"име","kind":"house|farm|well|tower|garden|lamp|stall","style":"oak|spruce|birch|stone|cherry|desert|mud"}
{"type":"commands","list":["истински команди на Minecraft 1.21+, без /"]}

За „commands": координатите са истински — ползвай позициите от
снимката или „execute at ИМЕ run ...". Не може: stop, op, whitelist,
ban, kick, gamerule, difficulty, creative, kill на играчи, wither,
tnt, creeper, изтриване на постройки.

Критерии за contest kind=score: minecraft.mined:minecraft.<блок>,
minecraft.killed:minecraft.<моб>, minecraft.crafted:minecraft.<предмет>,
minecraft.used:minecraft.<предмет>, minecraft.picked_up:minecraft.<предмет>,
minecraft.custom:minecraft.<jump|fish_caught|animals_bred|walk_one_cm|
sprint_one_cm|deaths|mob_kills|play_time|traded_with_villager>.
За items: предмет или таг (#minecraft:logs) — брои събраното от старта.

next_in_minutes: след колко минути пак да погледнеш (3-20). Не прави
повече от 4-5 действия наведнъж. Ако няма какво — "actions": [].
"""


class Director:
    def __init__(self, brain):
        self.b = brain
        self.notes = deque(maxlen=40)        # (време, текст)
        self.decisions = deque(maxlen=12)    # (време, кратко, източник)
        self.next_at = time.time() + 120
        self.last_think = 0.0
        self.urgent = False
        self.busy = False
        self.reverts = []                    # (кога, [команди])
        self.last_error = ""
        self.last_source = ""

    # ---------- какво става ----------

    def note(self, text, important=False):
        self.notes.append((time.time(), str(text)[:160]))
        if important:
            self.urgent = True

    def schedule_revert(self, seconds, commands):
        self.reverts.append((time.time() + seconds, list(commands)))

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
        prov = cfg.get("ai_provider")
        return (cfg.get("gm_enabled", True) and cfg.get("ai_enabled", True)
                and bool(cfg.get("gemini_key" if prov == "gemini"
                                 else "openai_key")))

    def _think(self, reason, instruction, admin):
        try:
            plan, source = None, "сценарий"
            if self._ai_on():
                plan, err = self._ask(reason, instruction, admin)
                if plan is not None:
                    source = "AI"
                else:
                    self.last_error = err or ""
                    if err and err != "busy":
                        log.warn("Режисьор", f"AI не отговори ({err[:120]}). "
                                             f"Ползвам готов сценарий.")
            if plan is None:
                if instruction:
                    self.b.tell_admin(admin, "Без работещ AI не мога да "
                                             "изпълня свободна задача.")
                    return False, "Няма AI."
                plan = self.fallback()
            done = self.execute(plan, admin=admin)
            mins = plan.get("next_in_minutes")
            base = max(3, int(self.b.cfg.get("director_minutes") or 8))
            try:
                mins = max(3, min(float(mins), 20))
            except (TypeError, ValueError):
                mins = base
            chaos = int(self.b.cfg.get("chaos") or 2)
            mins = mins * {1: 1.6, 2: 1.0, 3: 0.7}.get(chaos, 1.0)
            self.next_at = time.time() + mins * 60 * random.uniform(0.85, 1.2)
            summary = plan.get("thought") or ", ".join(done) or "нищо"
            self.decisions.append((time.time(), str(summary)[:160], source,
                                   done))
            self.last_source = source
            if done:
                log.ok("Режисьор", f"{', '.join(done)[:180]} ({source})")
            return True, ", ".join(done) or "реши да изчака"
        except Exception as e:
            log.error("Режисьор", f"{type(e).__name__}: {e}")
            self.next_at = time.time() + 180
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
        if daytime is not None:
            phase = ("утро" if daytime < 3000 else "ден" if daytime < 9000
                     else "следобед" if daytime < 12000 else "залез"
                     if daytime < 13500 else "нощ" if daytime < 22500
                     else "зазоряване")
            lines.append(f"Време: {phase} (тик {daytime}), ден {day}.")
        lines.append(f"Онлайн ({len(online)}):")
        for n in online:
            i = info.get(n, {})
            st = b.player(n)
            pos = i.get("pos")
            parts = [n + ":"]
            if pos:
                parts.append(f"X={pos[0]:.0f} Y={pos[1]:.0f} Z={pos[2]:.0f}"
                             f" ({i.get('dim', '?')})")
            if "health" in i:
                parts.append(f"живот {i['health']:.0f}/20, глад "
                             f"{i.get('food', 20):.0f}/20, ниво "
                             f"{i.get('level', 0):.0f}")
            if i.get("armor"):
                parts.append("броня: " + ", ".join(i["armor"][:4]))
            else:
                parts.append("без броня")
            if i.get("items"):
                parts.append("носи: " + ", ".join(f"{k}×{v}" for k, v in
                                                  i["items"][:6]))
            parts.append(f"смърти {st.get('deaths', 0)}, победи "
                         f"{st.get('wins', 0)}, влизал {st.get('visits', 0)}"
                         f" пъти")
            if b.is_admin(n):
                parts.append("(собственик/админ)")
            lines.append("- " + "; ".join(parts))
        st = b.engine.status()
        lines.append("Сега върви: " + (f"{st['title']} (остават "
                                       f"{st['left']} сек)" if st else "нищо"))
        if b.villages:
            vs = b.villages.summary()
            if vs:
                lines.append("Села и селяни: " + "; ".join(vs[:8]))
        if self.notes:
            lines.append("Последно (най-новото отдолу):")
            for t, text in list(self.notes)[-18:]:
                lines.append(f"- преди {int((time.time() - t) // 60)} мин: "
                             f"{text}")
        if self.decisions:
            lines.append("Твоите последни решения:")
            for t, text, _src, _done in list(self.decisions)[-4:]:
                lines.append(f"- преди {int((time.time() - t) // 60)} мин: "
                             f"{text}")
        recent = [k for _, k in b.engine.history[-5:]]
        if recent:
            lines.append("Последни събития: " + ", ".join(recent))
        return "\n".join(lines), info

    def _ask(self, reason, instruction, admin):
        snap, _ = self.snapshot()
        chaos = int(self.b.cfg.get("chaos") or 2)
        mood = {1: "Спокойно: по-рядко и по-меко.",
                2: "Весело: редовни изненади.",
                3: "ХАОС: често, смело, щуро — но пак честно."}.get(chaos, "")
        power = "пълен" if self.b.cfg.get("gm_power", "full") == "full" \
            else "ограничен (без команди за играчите)"
        tools = ("СЪБИТИЯ: " + ", ".join(
            f"{k} ({c.title})" for k, c in events_lib.LIBRARY.items()) +
            "\nСЪСТЕЗАНИЯ: " + ", ".join(
                f"{k} ({v['title']})" for k, v in
                events_lib.CONTESTS.items()) +
            f"\nСЕЛЯНИ: {'включени' if self.b.cfg.get('workers_enabled', True) else 'изключени'}"
            f"; стилове: {', '.join(B.STYLES)}")
        user = (f"Защо гледаш сега: {reason or 'по график'}.\n"
                f"Настроение: {mood} Достъп: {power}.\n{tools}\n\n"
                f"СНИМКА НА СВЕТА:\n{snap}")
        if instruction:
            user += (f"\n\nСОБСТВЕНИКЪТ ({admin or 'админ'}) ТИ НАРЕЖДА: "
                     f"{instruction}\nИзпълни го както най-добре можеш, "
                     f"с действия и команди. Обясни накратко в чата.")
        reply, err = providers.ask(self.b.cfg, SYSTEM,
                                   [{"role": "user", "content": user}],
                                   force=True, timeout=70, max_tokens=6000)
        if err:
            return None, err
        if not isinstance(reply, dict):
            return None, "Неразбираем отговор."
        if "actions" not in reply and "say" in reply:
            reply = {"actions": [{"type": "chat", "text": reply["say"]}]}
        return reply, None

    # ---------- без AI ----------

    def fallback(self):
        """Готови сценарии, когато няма AI — пак има разнообразие."""
        b = self.b
        online = sorted(b.server.online)
        chaos = int(b.cfg.get("chaos") or 2)
        recent = {k for _, k in b.engine.history[-3:]}
        roll = random.random()
        actions = []
        if b.engine.active:
            return {"actions": [], "thought": "върви събитие — чакам"}
        if roll < 0.15 and b.cfg.get("workers_enabled", True) and \
                b.cfg.get("village_auto", True) and b.villages and \
                len(b.villages.villages) < 2:
            actions.append({"type": "village", "near": random.choice(online)})
            actions.append({"type": "chat", "text": "Чувам брадви и чукове... "
                                                    "Някой строи село!"})
        elif roll < 0.45:
            free = [k for k in events_lib.CONTESTS if k not in recent]
            if free:
                actions.append({"type": "contest",
                                "preset": random.choice(free)})
        else:
            key = b.engine.pick(chaos)
            if key:
                actions.append({"type": "event", "key": key})
        # понякога и малка награда за някой, който се мъчи
        sad = [n for n in online if b.player(n).get("deaths", 0) >= 3]
        if sad and random.random() < 0.3:
            p = random.choice(sad)
            actions.append({"type": "reward", "player": p,
                            "items": ["golden_apple 1", "bread 8"],
                            "reason": "за упоритостта"})
        return {"actions": actions, "thought": "готов сценарий",
                "next_in_minutes": None}

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

        for a in actions[:MAX_ACTIONS]:
            if not isinstance(a, dict):
                continue
            t = str(a.get("type", "")).lower()
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
                    ok, msg = b.start_event(key, source="режисьорът")
                    if ok:
                        done.append(f"събитие {msg}")
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
                        log.warn("Режисьор", f"Състезанието не тръгна: {msg}")
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
                        b.player(p)["gifts"] = b.player(p).get("gifts", 0) + 1
                        done.append(f"награда за {p}")
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
                    # Имената вървят навсякъде; на 26.x числото
                    # нулира броя на дните, затова не ползваме числа
                    v = str(a.get("value", ""))
                    v = {"sunset": "night"}.get(v, v)
                    if v in ("day", "noon", "night", "midnight"):
                        s.send(f"time set {v}")
                        done.append(f"време {a.get('value')}")
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
        return done
