"""
spirits.py — духовете обикалят света сами.

На всеки минута-две един от духовете се появява до някой играч — в облак
от частици, обърнат към него — казва нещо за това, което играчът прави
(живот, инвентар, смърти, задачи, нощ), и според характера си:
  Пазителят — дава задача или подарък;
  Шегаджията — прави номер;
  Мара — продава: истинска търговия (десен бутон върху нея);
  Майсторът — праща селянин да построи нещо до играча.
След около минута изчезва. Ако играчът му говори, остава още малко.
"""

import random
import threading
import time

from ..ai import characters
from ..logbus import log
from . import world

STAY = 60
ORDER = ["keeper", "trickster", "trader", "builder", "keeper", "trickster"]

LINES = {
    "keeper": ["{p}, следя те отдавна. Светът има нужда от теб.",
               "Здравей, {p}. Звездите шепнат името ти.",
               "{p}! Имам нещо за теб, ако си достатъчно смел."],
    "trickster": ["Бау! Хаха, {p}, видях как подскочи!",
                  "{p}, имаш нещо на лицето... шегувам се. Или не?",
                  "Хехе, {p}. Скучно ли ти е? Сега ще ти стане весело!"],
    "trader": ["{p}, скъпи! Мара има стока, каквато не си виждал!",
               "Ела, ела, {p}! Само днес — цени за приятели.",
               "{p}, имам нещо специално. Погледни, само поглед!"],
    "builder": ["{p}, тук е голо. Ще пратя човек да ти направи нещо.",
                "Хм, {p}, мястото е добро. Заслужава постройка.",
                "{p}! Ръцете ме сърбят. Ще ти построим нещо."],
}
NIGHT = ["Нощта идва, {p}. Чудовищата вече те надушват.",
         "{p}, тъмно е, а ти си без броня. Смело или глупаво?"]
HURT = ["{p}, кървиш. Почини си, преди да е късно.",
        "Ох, {p}, изглеждаш зле. Хапни нещо."]
GIFTS = ["bread 6", "golden_apple 1", "torch 16", "cooked_beef 8",
         "iron_ingot 4", "emerald 3", "arrow 16", "experience_bottle 4"]
TRADES = [("emerald 2", "diamond 1"), ("emerald 6", "golden_apple 2"),
          ("iron_ingot 8", "emerald 3"), ("wheat 20", "emerald 2"),
          ("emerald 10", "totem_of_undying 1"), ("emerald 4", "saddle 1"),
          ("emerald 3", "name_tag 1"), ("rotten_flesh 24", "emerald 2"),
          ("emerald 5", "ender_pearl 2"), ("bone 16", "emerald 2"),
          ("emerald 8", "trident 1"), ("emerald 2", "glow_berries 8")]

VISIT_PROMPT = """
Сега си се появил САМ до играча {p} (той не те е викал) — изненада.
Кажи му нещо кратко и живо, свързано с това, което правиш като герой и с
това, което виждаш при него (време, живот, инвентар, смърти, задачи).
Не повтаряй последните си реплики.

Отговаряш САМО с JSON:
{{"say": "едно-две изречения", {extra}}}
"""
EXTRA = {
    "keeper": '"quest": null или {"kind": "collect|kill|mine|visit", '
              '"target": "предмет/моб/блок", "amount": 5, "title": "кратко", '
              '"text": "защо ти трябва", "reward": ["diamond 2"], "fame": 15}, '
              '"gift": null или "предмет брой"',
    "trickster": '"prank": true или false, "effect": null или {"effect": '
                 '"levitation", "seconds": 4}',
    "trader": '"offers": [{"buy": "emerald 4", "sell": "diamond 1"}] — 2 или 3 '
              'сделки, честни и интересни',
    "builder": '"build": null или едно от "lamp", "well", "garden", "house"',
}


class Visits:
    def __init__(self, brain):
        self.b = brain
        self.s = brain.s
        self.active = None
        self.next_at = time.time() + 75
        self.last_for = {}
        self.turn = 0
        self.busy = False
        self.log = []                     # (време, дух, играч, реплика)

    # ---------- кога ----------

    def interval(self):
        chaos = int(self.b.cfg.get("chaos") or 0)
        return {1: 260, 2: 140, 3: 80}.get(chaos)

    def tick(self):
        cfg = self.b.cfg
        if not cfg.get("spirits_roam", True):
            if self.active:
                self.leave()
            return
        a = self.active
        if a:
            online = self.b.server.online
            ppos = self.b.pos.get(a["player"])
            far = ppos and a.get("pos") and world.distance(ppos, a["pos"]) > 24
            if time.time() > a["until"] or a["player"] not in online or far:
                self.leave()
            return
        every = self.interval()
        if not every or self.busy or time.time() < self.next_at:
            return
        if not self.b.server.online:
            return
        self.next_at = time.time() + every * random.uniform(0.75, 1.25)
        self.start()

    # ---------- идване ----------

    def start(self, key=None, player=None, reason="", line=None, act=True):
        """line=None — AI измисля репликата; "" — идва мълчаливо;
        act=False — само идва и говори, без задачи, номера и подаръци."""
        online = sorted(self.b.server.online)
        if not online:
            return False, "няма никой"
        if self.busy:
            return False, "дух вече идва"
        if self.active:
            self.leave()
        if not key:
            key = ORDER[self.turn % len(ORDER)]
            self.turn += 1
        if key not in characters.CHARACTERS:
            return False, "няма такъв дух"
        if player not in online:
            player = min(online, key=lambda p: self.last_for.get(p, 0))
        self.last_for[player] = time.time()
        self.busy = True
        threading.Thread(target=self._go,
                         args=(key, player, reason, line, act),
                         daemon=True).start()
        return True, f"{characters.CHARACTERS[key]['name']} отива при {player}"

    def _go(self, key, player, reason, line, act=True):
        try:
            self._arrive(key, player, reason, line, act)
        except Exception as e:
            log.warn("Духове", f"{type(e).__name__}: {e}")
        finally:
            self.busy = False

    def _context(self, player):
        info = world.players_info(self.s, [player]).get(player, {})
        st = self.b.player(player)
        bits = []
        if info.get("health") is not None:
            bits.append(f"живот {info['health']:.0f}/20, глад "
                        f"{info.get('food', 20):.0f}/20, ниво "
                        f"{info.get('level', 0):.0f}")
        bits.append("броня: " + (", ".join(info.get("armor", [])) or "няма"))
        if info.get("items"):
            bits.append("носи: " + ", ".join(f"{k}×{v}" for k, v in
                                            info["items"][:6]))
        bits.append(f"смърти {st.get('deaths', 0)}, слава {st.get('fame', 0)}")
        try:
            q = self.b.quests.describe(player)
            bits.append(f"задачи: {q}")
        except Exception:
            pass
        daytime, _ = world.time_of_day(self.s)
        if daytime is not None:
            bits.append("нощ" if 13000 <= daytime <= 23000 else "ден")
        return info, "; ".join(bits)

    def _arrive(self, key, player, reason, line, act=True):
        ch = characters.CHARACTERS[key]
        info, ctx = self._context(player)
        reply = None
        if line is None and self.b._ai_ready() and \
                self.b.cfg.get("ai_enabled", True):
            recent = [t for _, k, _, t in self.log[-6:] if k == key]
            prompt = VISIT_PROMPT.format(p=player, extra=EXTRA[key])
            system = characters.system_prompt(
                key, f"{prompt}\nИграчът: {ctx}\n" +
                (f"Причина: {reason}\n" if reason else "") +
                (f"Твоите последни реплики: {recent}" if recent else ""))
            from ..ai import providers
            reply, _err = providers.ask(
                self.b.cfg, system, [{"role": "user",
                                      "content": f"(появяваш се до {player})"}],
                role="fast", max_tokens=900, timeout=30)
        reply = reply if isinstance(reply, dict) else {}
        say = line if line == "" else \
            (line or reply.get("say") or self._canned(key, player, info))
        say = str(say)[:220]

        offers = None
        if key == "trader":
            offers = self._offers(reply.get("offers"))
        tag = f"bvg_{key}_v"
        self._summon(key, player, tag, offers)
        if say:
            world.say(self.s, key, say)
            world.bubble_tag(self.s, tag, say, 2.4)
        self.active = {"key": key, "player": player, "tag": tag,
                       "until": time.time() + STAY, "made": time.time(),
                       "pos": getattr(self, "_pos", None) or
                       self.b.pos.get(player)}
        self.log.append((time.time(), key, player, say or "(дойде)"))
        self.log = self.log[-30:]
        log.info("Духове", f"{ch['name']} се появи при {player}")
        try:
            self.b.director.note(f"{ch['name']} навести {player}: "
                                 f"{say[:70]}")
        except Exception:
            pass
        if act:
            self._act(key, player, reply, info)

    def _canned(self, key, player, info):
        hp = info.get("health")
        if key == "keeper" and hp is not None and hp < 8:
            return random.choice(HURT).format(p=player)
        if key == "keeper" and not info.get("armor") and random.random() < 0.4:
            return random.choice(NIGHT).format(p=player)
        return random.choice(LINES[key]).format(p=player)

    def _offers(self, raw):
        out = []
        for o in (raw or [])[:3]:
            if isinstance(o, dict) and world.item_spec(o.get("buy")) and \
                    world.item_spec(o.get("sell")):
                out.append((o["buy"], o["sell"]))
        from ..ai import safety
        out = [(b, s) for b, s in out
               if world.item_spec(s)[0] not in safety.NEVER_ITEMS]
        return out or random.sample(TRADES, 3)

    def _summon(self, key, player, tag, offers):
        ch = characters.CHARACTERS[key]
        recipes = ""
        if offers:
            parts = []
            for buy, sell in offers:
                bi, bn = world.item_spec(buy)
                si, sn = world.item_spec(sell)
                parts.append(f'{{buy:{{id:"minecraft:{bi}",count:{bn}}},'
                             f'sell:{{id:"minecraft:{si}",count:{sn}}},'
                             f"maxUses:4,rewardExp:0b}}")
            recipes = ",".join(parts)
        self.s.send(f"kill @e[tag={tag}]")
        self.s.send(f"kill @e[tag={tag}_bubble]")
        self.s.send(
            f"execute at {player} rotated as {player} rotated ~ 0 positioned "
            f"^ ^ ^2.5 run summon villager ~ ~ ~ "
            f'{{CustomName:{{text:"{world.esc(ch["name"])}",'
            f'color:"{ch["color"]}",bold:true}},CustomNameVisible:1b,'
            f'Tags:["{tag}","bvg_npc","bvg_visit"],NoAI:1b,Invulnerable:1b,'
            f"Silent:1b,PersistenceRequired:1b,"
            f'VillagerData:{{profession:"minecraft:{ch["profession"]}",'
            f'level:5,type:"minecraft:plains"}},'
            f"Offers:{{Recipes:[{recipes}]}}}}")
        self.s.send(f"execute as @e[tag={tag},limit=1] at @s run tp @s "
                    f"~ ~ ~ facing entity {player} eyes")
        self.s.send(f"execute at @e[tag={tag},limit=1] run particle "
                    f"minecraft:end_rod ~ ~1 ~ 0.4 0.9 0.4 0.05 40",
                    optional=True)
        self.s.send(f"execute at @e[tag={tag},limit=1] run playsound "
                    f"minecraft:block.amethyst_block.chime neutral @a "
                    f"~ ~ ~ 1 0.7", optional=True)
        pos = world.tag_pos(self.s, tag)
        if self.active is not None:
            self.active["pos"] = pos
        self._pos = pos

    def _act(self, key, player, reply, info):
        b = self.b
        giver = {"name": characters.CHARACTERS[key]["name"],
                 "color": characters.CHARACTERS[key]["color"],
                 "kind": "spirit", "ref": f"s_{key}"}
        if key == "keeper":
            q = reply.get("quest")
            if isinstance(q, dict) and q.get("kind"):
                b.quests.give(player, q.get("kind"), q.get("target"),
                              q.get("amount"), q.get("title"), q.get("text"),
                              q.get("reward"), 30, q.get("fame", 15), giver,
                              minutes=25, at=q.get("at"))
            elif not reply and random.random() < 0.5:
                self._template_quest(player, giver)
            gift = reply.get("gift") if reply else \
                (random.choice(GIFTS) if random.random() < 0.4 else None)
            if gift and world.item_spec(gift):
                it, n = world.item_spec(gift)
                from ..ai import safety
                if it not in safety.NEVER_ITEMS:
                    self.s.send(f"give {player} minecraft:{it} {min(n, 16)}")
        elif key == "trickster":
            if reply.get("prank", not reply) or not reply:
                b.prank(player)
            eff = reply.get("effect")
            if isinstance(eff, dict):
                from ..ai import safety
                cmd = (f"effect give {player} minecraft:"
                       f"{str(eff.get('effect', 'levitation'))[:30]} "
                       f"{max(1, min(int(eff.get('seconds') or 4), 15))} 0 true")
                ok, _ = safety.check([cmd], ["effects"])
                b.s.send_many(ok)
        elif key == "builder":
            kind = reply.get("build") if reply else \
                random.choice(["lamp", "lamp", "well", "garden"])
            if kind in ("lamp", "well", "garden", "house") and b.villages:
                b.villages.request_build(player, kind, owner="Майсторът")

    def _template_quest(self, player, giver):
        from .quests import VILLAGER_WANTS
        kind, target, amount, reward = random.choice(VILLAGER_WANTS["*"])
        self.b.quests.give(player, kind, target, amount,
                           text="Докажи, че си достоен.", reward=reward,
                           fame=15, giver=giver, minutes=25)

    # ---------- тръгване ----------

    def leave(self):
        a = self.active
        self.active = None
        if not a:
            return
        tag = a["tag"]
        self.s.send(f"execute at @e[tag={tag},limit=1] run particle "
                    f"minecraft:poof ~ ~1 ~ 0.3 0.6 0.3 0.02 25",
                    optional=True)
        self.s.send(f"kill @e[tag={tag}]")
        self.s.send(f"kill @e[tag={tag}_bubble]")

    def stay(self, seconds=45):
        if self.active:
            self.active["until"] = max(self.active["until"],
                                       time.time() + seconds)

    def near(self, pos, radius=9):
        a = self.active
        if a and pos and a.get("pos") and \
                world.distance(pos, a["pos"]) <= radius:
            return a["key"]
        return None

    def giver_positions(self):
        a = self.active
        return {f"s_{a['key']}": a["pos"]} if a and a.get("pos") else {}

    def status(self):
        a = self.active
        return {"active": {"spirit": characters.CHARACTERS[a["key"]]["name"],
                           "player": a["player"],
                           "left": int(a["until"] - time.time())} if a else None,
                "recent": [{"ago": int(time.time() - t),
                            "spirit": characters.CHARACTERS[k]["name"],
                            "player": p, "say": say}
                           for t, k, p, say in self.log[-6:]][::-1]}
