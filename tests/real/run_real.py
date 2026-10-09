"""
run_real.py — проверка на BVG Craft срещу ИСТИНСКИ Paper сървър.

Върви в GitHub Actions (там има достъп до сайтовете на Paper и Mojang).
1. Сваля Paper със същия код, който ползва приложението.
2. Пуска сървъра през приложението (истинска конзола, истински RCON).
3. Праща на сървъра всяка команда, която приложението може да
   произведе (чертежи, събития, състезания, режисьор, проби), и
   записва всяка, която сървърът не разбира.
4. Ако mineflayer поддържа версията — влизат ботове-играчи: пишат на
   кирилица, скачат, искат строежи, участват в събития и състезания.
5. Пише отчет (markdown + json).

AI-то е заменено с предвидим „сценарий", за да се проверява кодът,
а не настроението на модела.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.modules.setdefault("webview", types.ModuleType("webview"))

from app import paths  # noqa: E402

WORK = tempfile.mkdtemp(prefix="bvgreal")
paths.DATA = os.path.join(WORK, "data")
paths.SERVER = os.path.join(WORK, "server")
paths.JDK = os.path.join(WORK, "jdk")
paths.CONFIG = os.path.join(paths.DATA, "config.json")
paths.LOG = os.path.join(paths.DATA, "log.txt")
for d in (paths.DATA, paths.SERVER):
    os.makedirs(d, exist_ok=True)

from app.server import manager  # noqa: E402
from app.server.rcon import classify_reply  # noqa: E402
from app.ai import providers, director as director_mod  # noqa: E402
from app.ai import brain as brain_mod  # noqa: E402
from app.game import blueprints, events_lib, guide, world  # noqa: E402
from app.logbus import log  # noqa: E402

RESULTS = []          # (раздел, име, успех, подробности)
BAD = []              # (откъде, команда, отговор)
NONE = "@a[tag=bvg_none,limit=1]"


def check(section, name, ok, detail=""):
    RESULTS.append((section, name, bool(ok), str(detail)[:400]))
    print(f"  {'✓' if ok else '✗'} [{section}] {name}"
          f"{'' if ok else '  <- ' + str(detail)[:200]}", flush=True)
    return ok


def wait(pred, timeout, step=0.5):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


# ---------------------------------------------------------------------------
# Сценарий вместо AI
# ---------------------------------------------------------------------------

POS_IN_CTX = re.compile(r"X=(-?\d+) Y=(-?\d+) Z=(-?\d+)")
GM_PLANS = []         # следващите планове на режисьора (FIFO)


def fake_ask(cfg, system, messages, audio=None, timeout=45, force=False,
             max_tokens=4096):
    last = messages[-1]["content"]
    providers._note_ok("сценарий")
    if "РЕЖИСЬОРЪТ" in system:
        if GM_PLANS:
            return GM_PLANS.pop(0), None
        return {"thought": "нищо", "actions": [], "next_in_minutes": 20}, None
    if "селянин-строител" in system:
        return {"say": "Добре, ще ти направя малка къща!",
                "job": {"kind": "house", "style": "spruce",
                        "size": "small"}}, None
    if "умря" in last:
        return {"say": "Класика."}, None
    if "Майсторът" in system:
        m = POS_IN_CTX.search(system)
        if m:
            x, y, z = (int(v) for v in m.groups())
            return {"say": "Ето ти кула.", "commands": [
                f"fill {x + 6} {y} {z + 6} {x + 8} {y + 4} {z + 8} "
                f"minecraft:stone_bricks hollow",
                f"setblock {x + 7} {y + 5} {z + 7} minecraft:lantern",
                f"give {last.split(':')[0]} minecraft:diamond 64"]}, None
    return {"say": "Проба от духа."}, None


providers.ask = fake_ask


# ---------------------------------------------------------------------------
# Улавяне на команди без сървър
# ---------------------------------------------------------------------------

class Capture:
    """Събира командите; на въпроси отговаря правдоподобно."""

    def __init__(self):
        self.cmds = []
        self.enabled = True

    def send(self, c, optional=False):
        self.cmds.append(c)

    def send_many(self, cs, optional=False):
        self.cmds.extend(cs)

    def query(self, c):
        self.cmds.append(c)
        if "Pos" in c:
            return True, "X has the following entity data: [10.5d, 70.0d, 10.5d]"
        if "Rotation" in c:
            return True, "X has the following entity data: [90.0f, 0.0f]"
        if "execute if" in c:
            return True, "Test passed, count: 1"
        return True, ""

    def query_many(self, cs):
        return [self.query(c) for c in cs]


def validate(sender, origin, cmds, subst=None):
    """Праща командите на истинския сървър; записва неразбраните."""
    uniq = []
    seen = set()
    for c in cmds:
        if subst:
            for a, b in subst.items():
                c = re.sub(rf"(?<![\w\"]){re.escape(a)}(?![\w])", b, c)
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    bad = []
    res = sender.query_many(uniq) if uniq else []
    for c, (ok, r) in zip(uniq, res):
        if not ok:
            bad.append((c, r))
        elif classify_reply(r) == "syntax":
            bad.append((c, r))
    for c, r in bad:
        BAD.append((origin, c, r))
    return len(uniq), bad


# ---------------------------------------------------------------------------
# Ботове
# ---------------------------------------------------------------------------

class Bots:
    def __init__(self, names):
        self.events = []
        self.p = subprocess.Popen(
            ["node", os.path.join(HERE, "bots.js"), "run", "25565",
             ",".join(names)], cwd=HERE, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            line = line.strip()
            try:
                self.events.append(json.loads(line))
            except ValueError:
                if line:
                    self.events.append({"ev": "raw", "text": line})

    def cmd(self, **kw):
        try:
            self.p.stdin.write(json.dumps(kw, ensure_ascii=False) + "\n")
            self.p.stdin.flush()
        except OSError:
            pass

    def spawned(self):
        return {e["bot"] for e in self.events if e.get("ev") == "spawn"}

    def got(self, bot, needle):
        return any(e.get("ev") == "msg" and e.get("bot") == bot and
                   needle in e.get("text", "") for e in self.events)

    def stop(self):
        try:
            self.p.terminate()
        except Exception:
            pass


def node_supports(version):
    try:
        out = subprocess.run(["node", os.path.join(HERE, "bots.js"), "check",
                              version], cwd=HERE, capture_output=True,
                             text=True, timeout=60)
        return out.stdout.strip() == "yes"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Версия на Paper
# ---------------------------------------------------------------------------

def resolve_version(want):
    versions = manager.list_versions()
    print("Paper версии:", versions[:12], flush=True)
    if not versions:
        return None
    if want == "latest":
        return versions[0]
    exact = [v for v in versions if v == want]
    if exact:
        return exact[0]
    fam = [v for v in versions if v.startswith(want + ".") or v == want]
    return fam[0] if fam else None


# ---------------------------------------------------------------------------
# Проверките
# ---------------------------------------------------------------------------

def catalog(api):
    """Всяка команда, която приложението може да произведе."""
    s = api._sender
    sec = "Команди"

    # 1) Чертежите: всеки блок във всяко завъртане
    cmds = []
    for b in sorted(blueprints.all_blocks()):
        for r in range(4):
            cmds.append(f"setblock 0 318 0 {blueprints.ns(blueprints.rot_state(b, r))}")
    cmds += [f"fill 0 318 0 1 318 1 minecraft:dirt_path replace "
             f"minecraft:grass_block"]
    for tree in ("birch", "oak", "cherry", "azalea_tree"):
        cmds.append(f"place feature minecraft:{tree} 0 319 0")
    cmds += world.clear_vegetation_cmds(0, 310, 0, 2, 312, 2)
    cmds.append("setblock 0 318 0 minecraft:air")
    n, bad = validate(s, "чертежи", cmds)
    check(sec, f"чертежи и блокове ({n})", not bad, bad[:3])

    # 2) Помощниците за света
    cap = Capture()
    world.say(cap, "keeper", 'Проба с "кавички" и \\ наклонена')
    world.speak(cap, "Бай Иван", "yellow", "проба")
    world.speak_near(cap, "Бай Иван", "yellow", "проба", (0, 100, 0))
    world.whisper(cap, NONE, "trickster", "тайна")
    world.tell(cap, NONE, "съобщение")
    world.announce(cap, "Заглавие", "подзаглавие", "gold", NONE)
    world.actionbar(cap, NONE, "лента")
    world.sound(cap, NONE, "minecraft:ui.toast.challenge_complete")
    world.firework(cap, NONE)
    world.reward(cap, NONE, ["diamond 2", "golden_apple 1"], 30, "Награда",
                 "тест")
    world.bubble_tag(cap, "bvg_probe", "балонче")
    world.spawn_villager(cap, "bvg_probe", "Проба", "yellow", "mason",
                         (0.5, 300, 0.5), 90, extra_tags=("bvg_worker",))
    for key in ("keeper", "builder", "trickster", "trader"):
        world.spawn_npc(cap, key, (0.5, 300, 0.5), 0)
        cap.cmds.append(f"kill @e[tag=bvg_{key}]")
    cap.cmds.append("kill @e[tag=bvg_probe]")
    cap.cmds.append(guide.give_command(NONE))
    for prank in brain_mod.PRANKS:
        cap.cmds += [c.format(p=NONE) for c in prank]
    n, bad = validate(s, "помощници", cap.cmds)
    check(sec, f"реплики, надписи, награди, селяни ({n})", not bad, bad[:3])

    # 3) Часът (височината и празното място — при играчите, там е заредено)
    t = world.time_of_day(s)
    check(sec, "час от денонощието", t[0] is not None,
          (t, s.query_many(["time query daytime", "time query day"])))

    # 4) Събитията (с измислен играч -> селектор)
    for key, cls in events_lib.LIBRARY.items():
        cap = Capture()
        eng = events_lib.Engine(cap, api._cfg, lambda: {"Probe_1", "Probe_2"})
        ok, msg = eng.start(key)
        if eng.active:
            eng.active.started -= 1                # да мине поне един тик
            for _ in range(3):
                try:
                    eng.active.tick()
                except Exception:
                    pass
            try:
                eng.active.finish("Probe_1")
            except TypeError:
                eng.active.finish("Probe_1")
        eng.stop()
        n, bad = validate(s, f"събитие {key}", cap.cmds,
                          {"Probe_1": NONE, "Probe_2": NONE})
        check("Събития", f"{key}: команди ({n})", ok and not bad,
              (msg, bad[:2]))

    # 5) Състезанията: всеки критерий наистина съществува
    for key, p in events_lib.CONTESTS.items():
        if p["kind"] == "score":
            res = s.query_many([
                "scoreboard objectives remove bvgprobe",
                f"scoreboard objectives add bvgprobe {p['criterion']} "
                f'{{"text":"{p["title"]}"}}',
                "scoreboard objectives remove bvgprobe"])
            r = res[1][1] if len(res) > 1 else ""
            ok = res and res[1][0] and classify_reply(r) == "ok"
        else:
            ok2, r = s.query(f"execute if items entity {NONE} container.* "
                             f"{p['item']}")
            ok = ok2 and classify_reply(r) != "syntax"
        if not ok:
            BAD.append((f"състезание {key}", p.get("criterion") or
                        p.get("item"), r))
        check("Състезания", f"{key} ({p['title']})", ok, r)

    # 6) Режисьорът: всички видове действия
    cap = Capture()
    fake = types.SimpleNamespace()
    fake.s = cap
    fake.cfg = api._cfg
    fake.server = types.SimpleNamespace(online={"Probe_1", "Probe_2"},
                                        ready=True)
    fake.engine = events_lib.Engine(cap, api._cfg,
                                    lambda: {"Probe_1", "Probe_2"})
    fake.villages = None
    fake.players = {}
    fake.player = lambda n: fake.players.setdefault(n, {"deaths": 0})
    fake.is_admin = lambda n: False
    fake.prank = lambda p: cap.send_many(
        [c.format(p=p) for c in brain_mod.PRANKS[0]])
    fake.start_event = lambda k, source="": fake.engine.start(k)
    fake.tell_admin = lambda *a, **k: None
    fake.cache_positions = lambda info: None
    d = director_mod.Director(fake)
    director_mod.MAX_ACTIONS = 30
    plan = gm_plan(["Probe_1", "Probe_2"])
    done = d.execute(plan)
    fake.engine.stop()
    n, bad = validate(s, "режисьор", cap.cmds,
                      {"Probe_1": NONE, "Probe_2": NONE})
    check("Режисьор", f"всички видове действия ({len(done)} изпълнени, "
                      f"{n} команди)", len(done) >= 10 and not bad,
          (done, bad[:3]))


def gm_plan(names):
    p, q = names[0], names[-1]
    return {"thought": "тест на всичко", "actions": [
        {"type": "announce", "title": "Тест на режисьора",
         "subtitle": "всичко работи", "color": "gold"},
        {"type": "chat", "text": "Проба от режисьора"},
        {"type": "whisper", "player": p, "text": "тайна"},
        {"type": "actionbar", "target": "@a", "text": "лента"},
        {"type": "reward", "player": p, "items": ["diamond 2",
                                                  "golden_apple 1"],
         "xp": 30, "reason": "тест"},
        {"type": "effect", "target": q, "effect": "speed", "seconds": 10,
         "level": 1},
        {"type": "size", "player": q, "scale": 1.5, "seconds": 8},
        {"type": "spawn", "mob": "chicken", "near": p, "count": 2,
         "name": "Пиле"},
        {"type": "weather", "value": "clear"},
        {"type": "time", "value": "day"},
        {"type": "prank", "player": q},
        {"type": "contest", "kind": "score",
         "criterion": "minecraft.mined:minecraft.coal_ore", "amount": 5,
         "minutes": 3, "title": "Въглищари", "desc": "5 въглища",
         "reward": ["emerald 3"]},
        {"type": "commands", "list": [
            f"execute at {p} run particle minecraft:heart ~ ~2 ~ 1 1 1 0 10",
            f"give {p} minecraft:bread 3",
            "op " + p, "kill @a"]},
    ], "next_in_minutes": 10}


def blueprint_match(s, job, limit=500):
    """Колко от блоковете по чертежа наистина стоят в света."""
    want = {}
    for op in job.plan.ops:
        if op[0] in ("F", "S"):
            if op[0] == "F":
                x1, y1, z1, x2, y2, z2 = op[1:7]
                block = op[7]
            else:
                x1, y1, z1 = x2, y2, z2 = op[1:4]
                block = op[4]
            name = block.split("[")[0]
            for x in range(min(x1, x2), max(x1, x2) + 1):
                for y in range(min(y1, y2), max(y1, y2) + 1):
                    for z in range(min(z1, z2), max(z1, z2) + 1):
                        want[(x, y, z)] = name
    ox, oy, oz = job.origin
    items = [(k, v) for k, v in want.items() if v != "air"]
    step = max(1, len(items) // limit)
    items = items[::step]
    cmds = []
    for (x, y, z), name in items:
        rx, rz = blueprints.rot_xz(x, z, job.rot)
        cmds.append(f"execute if block {ox + rx} {oy + y} {oz + rz} "
                    f"{blueprints.ns(name)}")
    res = s.query_many(cmds)
    good = sum(1 for ok, r in res if ok and "passed" in (r or "").lower())
    miss = [c for c, (ok, r) in zip(cmds, res)
            if not (ok and "passed" in (r or "").lower())][:5]
    return good / max(1, len(cmds)), len(cmds), miss


def players_phase(api, version):
    names = ["BVG", "Ivan_99", "Mia"]
    if not node_supports(version):
        check("Играчи", f"ботовете не поддържат {version} — пропуснато",
              True)
        return
    bots = Bots(names)
    try:
        _players(api, bots, names)
    finally:
        for n in names:
            bots.cmd(bot=n, quit=True)
        time.sleep(2)
        bots.stop()


def _players(api, bots, names):
    s, srv, br = api._sender, api._server, api._brain
    br.director.next_at = time.time() + 10 ** 6     # само когато ние кажем
    sec = "Играчи"
    ok = wait(lambda: bots.spawned() >= set(names), 120)
    check(sec, "ботовете влязоха", ok, [e for e in bots.events
                                       if e.get("ev") in ("kicked", "error",
                                                          "end")][:3])
    if not ok:
        return
    s.query_many(["effect give @a minecraft:resistance 900 4 true",
                  "effect give @a minecraft:saturation 900 1 true",
                  "time set 1000", "weather clear 6000",
                  "gamerule doDaylightCycle false"])
    check(sec, "засечени онлайн (3)",
          wait(lambda: set(srv.online) >= set(names), 30), srv.online)
    st = api.state()
    roster = {r["name"]: r for r in st["roster"]}
    check(sec, "IP на играчите", all(roster.get(n, {}).get("ip")
                                     for n in names),
          {n: roster.get(n, {}).get("ip") for n in names})
    check(sec, "поздрав при влизане", wait(
        lambda: any(bots.got(n, "Добре дошъл") for n in names), 20))
    check(sec, "книга за нов играч", wait(
        lambda: s.query("execute if items entity BVG container.* "
                        "minecraft:written_book")[1].lower()
        .startswith("test passed"), 15))
    br._refresh_positions()
    check(sec, "позиции на играчите", len(br.pos) == 3, br.pos)

    # четене от света до играч (заредени чънкове)
    bp = br.pos.get("BVG")
    if bp:
        bx, bz = int(bp[0]), int(bp[2])
        ys = world.surfaces(s, [(bx + 3, bz + 3), (bx - 4, bz + 2)])
        check(sec, "височина на земята до играча", all(y is not None
                                                        for y in ys), ys)
        empty = world.region_empty(s, bx + 20, int(bp[1]) + 30, bz + 20,
                                   bx + 23, int(bp[1]) + 34, bz + 23)
        check(sec, "проверка за празно място", empty, empty)

    # духове
    r = api.place("builder", "BVG")
    check("Духове", "призоваване до играч", r.get("ok") and wait(
        lambda: "passed" in s.query("execute if entity @e[tag=bvg_builder]")
        [1].lower(), 10), r)
    # говорене на кирилица -> строеж
    pos = br.pos.get("Ivan_99")
    px, py, pz = (int(f"{v:.0f}") for v in pos) if pos else (0, 0, 0)
    bots.cmd(bot="Ivan_99", chat="Майсторе, построй ми кула")
    built = wait(lambda: pos and "passed" in s.query(
        f"execute if block {px + 6} {py} {pz + 6} "
        f"minecraft:stone_bricks")[1].lower(), 20)
    check("Духове", "кирилица в чата -> Майсторът строи", built, pos)
    check("Духове", "Майсторът НЕ дава диаманти", "passed" not in s.query(
        "execute if items entity Ivan_99 container.* minecraft:diamond")[1]
        .lower())
    check("Духове", "отговорът на духа стига до играчите",
          wait(lambda: bots.got("Mia", "Ето ти кула"), 10))

    # команди от играта
    bots.cmd(bot="BVG", chat="!помощ")
    check("Команди от играта", "!помощ за админ",
          wait(lambda: bots.got("BVG", "!режисьор"), 10))
    bots.cmd(bot="Mia", chat="!село")
    check("Команди от играта", "не-админ не може", wait(
        lambda: bots.got("Mia", "само за админите"), 10))

    # строеж от селянин
    bots.cmd(bot="BVG", chat="!строй малка къща от смърч")
    started = wait(lambda: any(w.get("_job") for w in br.villages.workers),
                   90)
    check("Селяни", "селянинът намери място и почна", started,
          br.villages.status())
    if started:
        w = next(w for w in br.villages.workers if w.get("_job"))
        job = w["_job"]
        fin = wait(lambda: not w.get("_job"), 240, 1)
        check("Селяни", f"къщата е построена ({job.total} стъпки)", fin,
              br.villages.status())
        if fin:
            ox, oy, oz = job.origin
            door = br.villages._door_world(job)
            dx, dz = blueprints.rot_xz(job.plan.door[0], job.plan.door[1] - 1,
                                       job.rot)
            ok = "passed" in s.query(
                f"execute if block {ox + dx} {oy + 1} {oz + dz} "
                f"#minecraft:doors")[1].lower()
            check("Селяни", "вратата е на мястото си", ok,
                  (job.origin, job.rot, door))
            ok = "passed" in s.query(
                f"execute if block {ox} {oy} {oz} "
                f"minecraft:{blueprints.STYLES['spruce']['found']}")[1].lower()
            check("Селяни", "основата е от правилния камък", ok)
            ratio, n, miss = blueprint_match(s, job)
            check("Селяни", f"къщата съвпада с чертежа ({int(ratio * 100)}%"
                            f" от {n} блока, завъртане {job.rot})",
                  ratio >= 0.97, miss)

    # говорене със селянин
    w0 = br.villages.workers[0] if br.villages.workers else None
    if w0:
        bots.cmd(bot="Mia", chat=f"{w0['aliases'][0].capitalize()}, "
                                 f"как си?")
        check("Селяни", "селянинът отговаря по име",
              wait(lambda: bots.got("Mia", "малка къща"), 15))

    # ново село
    r = api.village_found("Mia")
    check("Села", "основаване на село", r.get("ok"), r)
    ok = wait(lambda: br.villages.villages and
              br.villages.villages[0]["buildings"], 200, 1)
    check("Села", "площадът е построен", ok, br.villages.status())
    if ok:
        v = br.villages.villages[0]
        b0 = v["buildings"][0]
        from app.game.villages import Job
        pj = Job("plaza", v.get("style", "oak"), "medium",
                 tuple(b0["origin"]), b0["rot"], 0)
        ratio, n, miss = blueprint_match(s, pj)
        check("Села", f"площадът съвпада с чертежа ({int(ratio * 100)}%, "
                      f"завъртане {b0['rot']})", ratio >= 0.97, miss)
        v["next_at"] = 0
        grew = wait(lambda: any(w.get("_job") and w.get("village") == v["id"]
                                for w in br.villages.workers), 120, 1)
        check("Села", "селото само расте (следваща постройка)", grew,
              br.villages.status())

    # събития с истински играчи
    before = len(s.rejected)
    for key in events_lib.LIBRARY:
        r = api.event(key)
        time.sleep(4)
        api.stop_event()
        time.sleep(0.5)
        check("Събития с играчи", key, r.get("ok"), r)
    s.query("kill @e[tag=bvg_undead]")
    s.query("kill @e[tag=bvg_giant]")
    syn = [x for x in list(s.rejected)[before:] if x[1] == "syntax"]
    check("Събития с играчи", "сървърът разбира всичко", not syn, syn[:3])

    # състезания
    r = api.contest("jumps")
    check("Състезания", "скачане тръгна", r.get("ok"), r)
    bots.cmd(bot="Ivan_99", jump=14)
    bots.cmd(bot="Mia", jump=6)
    # ботовете не винаги отскачат при всяко натискане — важното е, че
    # класацията брои истинските скокове от играта
    ok = wait(lambda: any(v > 0 for _, v in
                          (br.engine.status() or {}).get("standings", [])), 40)
    check("Състезания", "класацията брои скоковете", ok, br.engine.status())
    api.stop_event()
    r = api.contest("diamonds")
    check("Състезания", "диаманти тръгна", r.get("ok"), r)
    time.sleep(5)
    s.query("give Mia minecraft:diamond 3")
    ok = wait(lambda: br.engine.last_result and
              br.engine.last_result[1] == "Mia", 20)
    a = br.engine.active
    check("Състезания", "победителят е засечен и награден", ok,
          (br.engine.last_result, br.engine.status(),
           getattr(a, "base", None), getattr(a, "scores", None),
           s.query("execute if items entity Mia container.* "
                   "minecraft:diamond"),
           s.query("clear Mia minecraft:diamond 0")))
    api.stop_event()

    # режисьорът с „AI"
    director_mod.MAX_ACTIONS = 30
    GM_PLANS.append(gm_plan(["Ivan_99", "Mia"]))
    before = len(s.rejected)
    ok, msg = br.director.think("тест", wait=True)
    check("Режисьор", "изпълни плана", ok and "награда" in msg, msg)
    check("Режисьор", "играчите виждат репликата",
          wait(lambda: bots.got("BVG", "Проба от режисьора"), 10))
    check("Режисьор", "наградата стигна", "passed" in s.query(
        "execute if items entity Ivan_99 container.* minecraft:golden_apple")
        [1].lower())
    check("Режисьор", "отказа op и kill @a", "passed" not in s.query(
        "execute if entity @a[name=Ivan_99,gamemode=creative]")[1].lower())
    syn = [x for x in list(s.rejected)[before:] if x[1] == "syntax"]
    check("Режисьор", "сървърът разбира всичко", not syn, syn[:3])
    api.stop_event()
    # !ai задача от админа
    GM_PLANS.append({"actions": [{"type": "chat",
                                  "text": "Арената е готова"}]})
    bots.cmd(bot="BVG", chat="!ai направи ми малка арена")
    check("Режисьор", "!ai задача от играта",
          wait(lambda: bots.got("Mia", "Арената е готова"), 20))
    # без AI ключ -> готови сценарии
    api._cfg.data["gemini_key"] = ""
    ok, msg = br.director.think("тест без AI", wait=True)
    check("Режисьор", "работи и без AI (готов сценарий)",
          ok and br.director.last_source == "сценарий", msg)
    api.stop_event()
    api._cfg.data["gemini_key"] = "test"

    # конзола
    r = api.quick("op", "Mia")
    time.sleep(1.5)
    ops = open(os.path.join(paths.SERVER, "ops.json"), encoding="utf-8").read()
    check("Конзола", "OP с един бутон", r.get("ok") and "Mia" in ops, r)
    r = api.quick("survival", "Mia")
    check("Конзола", "бърза команда", r.get("ok"), r)

    # пълна проверка
    d = api.diagnose()
    bad = [c for c in d["checks"] if c["ok"] is False]
    check("Пълна проверка", f"{len(d['checks'])} проверки", not bad, bad)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mc", default="latest")
    ap.add_argument("--out", default="report")
    args = ap.parse_args()
    t0 = time.time()
    version = None
    api = None
    try:
        version = resolve_version(args.mc)
        check("Сървър", f"версия {args.mc} -> {version}", bool(version))
        if not version:
            return finish(args, version, t0)
        jar = manager.download_paper(version)
        check("Сървър", "Paper свален", bool(jar) and os.path.getsize(jar) >
              1_000_000, jar)
        from app.api import Api
        api = Api()
        api._cfg.data.update(
            rcon_port=25575, offline_mode=True, open_to_network=False,
            whitelist=["BVG", "Ivan_99", "Mia"], admins=["BVG"],
            owner_name="BVG", gemini_key="test", chaos=2,
            director_minutes=60, ram_gb=2, view_distance=6,
            simulation_distance=5, difficulty="easy")
        api._cfg.save()
        # Плосък свят: строежите и селата да са предвидими в теста
        with open(os.path.join(paths.SERVER, "server.properties"), "w") as f:
            f.write("level-type=minecraft\\:flat\ngenerate-structures=false\n"
                    "spawn-protection=0\n")
        r = api.start()
        check("Сървър", "пуска се", r.get("ok"), r)
        ok = wait(lambda: api._server.ready, 300, 1)
        check("Сървър", "готов (видя „Done“ или RCON)", ok,
              list(api._server.recent)[-5:])
        if not ok:
            return finish(args, version, t0, api)
        time.sleep(3)
        st = api.state()
        h = {x["name"]: x for x in st["health"]}
        check("Сървър", "конзолата се чете", h["Конзола"]["ok"], h["Конзола"])
        check("Сървър", "RCON отговаря", wait(
            lambda: api._monitor.rcon_ok, 20), h)
        args_txt = " ".join(manager.Server(api._cfg).build_command(
            "java", "paper.jar"))
        check("Сървър", "UTF-8 и без цветове", "-Dstdout.encoding=UTF-8" in
              args_txt)
        ok, reply = api._sender.query("say Проба на кирилица ЖЩЮЯ")
        check("Сървър", "кирилицата се връща от конзолата", wait(
            lambda: any("ЖЩЮЯ" in ln for ln in api._server.recent), 8),
            list(api._server.recent)[-3:])
        catalog(api)
        players_phase(api, version)
        syn = [x for x in api._sender.rejected if x[1] == "syntax"]
        check("Общо", "нито една неразбрана команда през цялото време",
              not syn, syn[:5])
    except Exception:
        check("Общо", "без сривове", False, traceback.format_exc())
    finally:
        if api:
            try:
                api._shutdown()
                check("Сървър", "спира чисто", not api._server.running)
            except Exception as e:
                check("Сървър", "спира чисто", False, e)
    finish(args, version, t0, api)


def finish(args, version, t0, api=None):
    ok = sum(1 for r in RESULTS if r[2])
    lines = [f"# Тест на истински сървър: {args.mc} -> {version}", "",
             f"**{ok}/{len(RESULTS)}** проверки минаха за "
             f"{int(time.time() - t0)} сек.", ""]
    sec = None
    for s, name, good, detail in RESULTS:
        if s != sec:
            lines += ["", f"## {s}"]
            sec = s
        lines.append(f"- {'✅' if good else '❌'} {name}" +
                     ("" if good else f"  \n  `{detail[:300]}`"))
    if BAD:
        lines += ["", "## Команди, които сървърът не разбра"]
        for origin, c, r in BAD[:80]:
            lines.append(f"- **{origin}**: `{c[:220]}`  \n  → `{r[:220]}`")
    if api:
        rej = list(api._sender.rejected)
        if rej:
            lines += ["", "## Отказани по време на играта (всички видове)"]
            for t, kind, c, r in rej[-60:]:
                lines.append(f"- {kind}: `{c[:200]}` → `{r[:200]}`")
        lines += ["", "## Последни редове от конзолата", "```"]
        lines += list(api._server.recent)[-40:]
        lines += ["```", "", "## Последни редове от лога", "```"]
        lines += [f"{e['t']} [{e['level']}] [{e['src']}] {e['msg']}"
                  for e in log.since(0, limit=100000)[-120:]]
        lines.append("```")
    with open(args.out + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(args.out + ".json", "w", encoding="utf-8") as f:
        json.dump({"mc": args.mc, "version": version, "ok": ok,
                   "total": len(RESULTS), "results": RESULTS,
                   "bad": BAD}, f, ensure_ascii=False, indent=1)
    print(f"\n{ok}/{len(RESULTS)}", flush=True)
    sys.exit(0 if ok == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
