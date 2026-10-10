"""
villages.py — селяни, които строят, и селата, които растат.

Селянинът е истински селянин в света. Като получи задача, търси място
наблизо, проверява го (равно ли е, сухо ли е, празно ли е — никога не
строи върху чужда постройка, дърво или вода) и строи блок по блок, а
играчите го гледат как работи. Селата растат сами с времето: площад,
къщи, ферма, кладенец, градина, кула, пътеки между тях.

Строи се само докато има играч наблизо: иначе светът там не е зареден
и сървърът отказва блоковете. Всичко се пази във villages.json, така че
след рестарт строежите продължават оттам, където са спрели.
"""

import json
import math
import os
import random
import threading
import time

from .. import paths
from ..logbus import log
from . import blueprints as B
from . import world

FILE = "villages.json"
MAX_WORKERS = 8
MAX_VILLAGES = 4
MAX_BUILDINGS = 12
NEAR_PLAYER = 112          # строи се само ако някой е по-близо от това
OPS_PER_TICK = 3
BLOCKS_PER_TICK = 90

NAMES = [("Бай Иван", ["иван", "иване", "бай иван", "бай иване"]),
         ("Дядо Стамат", ["стамат", "стамате", "дядо стамат"]),
         ("Баба Пена", ["пена", "пено", "баба пена"]),
         ("Чичо Добри", ["добри", "чичо добри"]),
         ("Леля Цонка", ["цонка", "цонке", "леля цонка"]),
         ("Майстор Кольо", ["кольо", "майстор кольо"]),
         ("Дядо Петко", ["петко", "дядо петко"]),
         ("Баба Гана", ["гана", "гано", "баба гана"]),
         ("Чичо Мирчо", ["мирчо", "чичо мирчо"]),
         ("Леля Ваня", ["ваня", "ваньо", "леля ваня"])]

VILLAGE_NAMES = ["Пикселово", "Горно Блокче", "Долно Кубче", "Каменица",
                 "Дъбрава", "Златна поляна", "Слънчево", "Криперово",
                 "Нови Майнове", "Стари Чанкове", "Кремъчна", "Тиквено",
                 "Медено", "Сребърен дол", "Лъчезар"]

VILLAGE_PLAN = ["plaza", "house", "farm", "house", "well", "lamp", "house",
                "garden", "stall", "house", "tower", "lamp", "house"]

PROFESSIONS = {"house": "mason", "tower": "mason", "well": "mason",
               "plaza": "mason", "farm": "farmer", "garden": "farmer",
               "lamp": "toolsmith", "stall": "cartographer"}

DONE_LINES = ["Готово! Като за сватба.", "Ето я. Здрава, хубава.",
              "Свърших. Кой ще черпи?", "Ха! Майсторска работа.",
              "Готово. Да ви е честито!"]
START_LINES = ["Хайде, почвам.", "Запретвам ръкави!", "Тук ще стане хубаво.",
               "Дай ми малко време.", "Мястото е добро. Почвам."]


class Job:
    def __init__(self, kind, style="oak", size="medium", origin=None, rot=0,
                 seed=0, step=0, found_min=None, village=None, owner=None,
                 label=None, custom=None):
        self.kind, self.style, self.size = kind, style, size
        self.origin, self.rot, self.seed = origin, rot, seed
        self.step, self.found_min = step, found_min
        self.village, self.owner = village, owner
        self.custom = custom
        if custom:
            self.plan = B.Plan("design", custom["w"], custom["d"],
                               custom["h"], [tuple(o) for o in custom["ops"]],
                               custom.get("label", "постройка"),
                               door=tuple(custom.get("door") or
                                          (custom["w"] // 2, custom["d"])))
        else:
            self.plan = B.make(kind, style, size, seed)
        self.label = label or self.plan.label
        self.started = time.time()
        self.prelude = self._prelude()

    def _prelude(self):
        """Основа под постройката, ако теренът е малко наклонен."""
        if self.origin is None or self.found_min is None:
            return []
        oy = self.origin[1]
        if self.found_min >= oy:
            return []
        p = self.plan
        return [("R", 0, self.found_min - oy, 0, p.w - 1, -1, p.d - 1,
                 STYLE_FOUND.get(self.style, "cobblestone"), "air"),
                ("R", 0, self.found_min - oy, 0, p.w - 1, -1, p.d - 1,
                 STYLE_FOUND.get(self.style, "cobblestone"), "water")]

    @property
    def ops(self):
        return self.prelude + self.plan.ops

    @property
    def total(self):
        return len(self.ops)

    @property
    def progress(self):
        return min(1.0, self.step / max(1, self.total))

    def to_dict(self):
        return {"kind": self.kind, "style": self.style, "size": self.size,
                "origin": self.origin, "rot": self.rot, "seed": self.seed,
                "step": self.step, "found_min": self.found_min,
                "village": self.village, "owner": self.owner,
                "label": self.label, "custom": self.custom}

    @classmethod
    def from_dict(cls, d):
        return cls(d["kind"], d.get("style", "oak"), d.get("size", "medium"),
                   tuple(d["origin"]) if d.get("origin") else None,
                   d.get("rot", 0), d.get("seed", 0), d.get("step", 0),
                   d.get("found_min"), d.get("village"), d.get("owner"),
                   d.get("label"), d.get("custom"))


STYLE_FOUND = {k: v["found"] for k, v in B.STYLES.items()}

# Истинска търговия със селяните — десен бутон върху тях
PROF_OFFERS = {
    "mason": [("emerald 1", "stone_bricks 16"), ("clay_ball 10", "emerald 1"),
              ("emerald 2", "polished_andesite 16"),
              ("emerald 3", "chiseled_stone_bricks 8"),
              ("emerald 4", "lantern 4")],
    "farmer": [("wheat 20", "emerald 1"), ("emerald 1", "bread 6"),
               ("emerald 2", "cake 1"), ("emerald 3", "golden_carrot 4"),
               ("carrot 22", "emerald 1"), ("emerald 2", "pumpkin_pie 4")],
    "toolsmith": [("coal 15", "emerald 1"), ("emerald 3", "iron_pickaxe 1"),
                  ("emerald 7", "diamond_pickaxe 1"),
                  ("emerald 2", "iron_shovel 1")],
    "cartographer": [("paper 24", "emerald 1"), ("emerald 4", "map 1"),
                     ("emerald 5", "compass 1"), ("emerald 6", "spyglass 1")],
}
ROAM_NOTE = "Селяните, които не строят, се разхождат свободно из селото."


class Villages:
    def __init__(self, sender, cfg, positions_fn, online_fn):
        self.s = sender
        self.cfg = cfg
        self.positions = positions_fn      # -> {име: (x, y, z)}
        self.online = online_fn            # -> set(имена)
        self._lock = threading.RLock()
        self.villages = []
        self.workers = []
        self.next_id = 1
        self.searching = {}                # id на селянин -> какво търси
        self.reserved = []                 # заети площи (x1, z1, x2, z2)
        self._dirty = False
        self._saved_at = 0.0
        self.load()

    # ---------- запазване ----------

    @property
    def path(self):
        return os.path.join(paths.DATA, FILE)

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        self.villages = data.get("villages", [])
        self.workers = data.get("workers", [])
        self.next_id = data.get("next_id", 1)
        for w in self.workers:
            if w.get("job"):
                try:
                    w["_job"] = Job.from_dict(w["job"])
                except Exception:
                    w["job"] = None
        self.reserved = [tuple(b["bbox"]) for v in self.villages
                         for b in v.get("buildings", []) if b.get("bbox")]
        self.reserved += [tuple(w["box"]) for w in self.workers
                          if w.get("_job") and w.get("box")]

    def save(self, force=False):
        if not force and (not self._dirty or time.time() - self._saved_at < 8):
            return
        with self._lock:
            for w in self.workers:
                j = w.get("_job")
                w["job"] = j.to_dict() if j else None
            data = {"villages": self.villages,
                    "workers": [{k: v for k, v in w.items()
                                 if not k.startswith("_")}
                                for w in self.workers],
                    "next_id": self.next_id}
        try:
            os.makedirs(paths.DATA, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
            self._dirty = False
            self._saved_at = time.time()
        except OSError as e:
            log.warn("Села", f"Не мога да запазя: {e}")

    def reset(self):
        """След внасяне на друг свят — старите места не важат."""
        with self._lock:
            self.villages, self.workers, self.reserved = [], [], []
            self.searching.clear()
        self._dirty = True
        self.save(force=True)

    def _new_id(self):
        i = self.next_id
        self.next_id += 1
        return i

    # ---------- селяни ----------

    def _tag(self, w):
        return f"bvg_w{w['id']}"

    def _free_name(self):
        used = {w["name"] for w in self.workers}
        free = [n for n in NAMES if n[0] not in used]
        return random.choice(free) if free else random.choice(NAMES)

    def _spawn(self, w, pos, yaw=0.0):
        if not w.get("offers"):
            pool = PROF_OFFERS.get(w.get("prof", "mason"), PROF_OFFERS["mason"])
            w["offers"] = random.sample(pool, min(3, len(pool)))
        world.spawn_villager(self.s, self._tag(w), w["name"], "yellow",
                             w.get("prof", "mason"), pos, yaw,
                             extra_tags=("bvg_worker",),
                             offers=w.get("offers"))
        w["pos"] = list(pos)
        w["free"] = False

    def _free(self, w):
        """Без работа — пуска го да се разхожда като истински селянин."""
        world.set_ai(self.s, self._tag(w), True)
        w["free"] = True

    def new_worker(self, near_pos, village=None, prof="mason"):
        with self._lock:
            if len(self.workers) >= MAX_WORKERS:
                # взимаме най-дълго свободния
                idle = [w for w in self.workers if not w.get("_job")
                        and w["id"] not in self.searching]
                if not idle:
                    return None
                w = min(idle, key=lambda w: w.get("idle_since", 0))
                w["village"] = village
                return w
            name, aliases = self._free_name()
            w = {"id": self._new_id(), "name": name, "aliases": aliases,
                 "village": village, "prof": prof, "pos": list(near_pos),
                 "idle_since": time.time(), "built": 0}
            self.workers.append(w)
        self._spawn(w, near_pos)
        self._dirty = True
        log.ok("Села", f"{name} дойде да работи.")
        return w

    def find_worker(self, text):
        """Селянинът, когото викат по име в съобщението."""
        import re
        low = (text or "").lower()
        best = None
        for w in self.workers:
            for a in w.get("aliases", []):
                m = re.search(rf"(?<!\w){re.escape(a)}(?!\w)", low)
                if m and (best is None or m.start() < best[1]):
                    best = (w, m.start())
        return best[0] if best else None

    def worker_near(self, pos, radius=6):
        best, bd = None, radius
        for w in self.workers:
            d = world.distance(pos, w.get("pos"))
            if d < bd:
                best, bd = w, d
        return best

    def worker(self, wid):
        return next((w for w in self.workers if w["id"] == wid), None)

    # ---------- търсене на място ----------

    def _overlaps(self, box):
        for r in self.reserved:
            if not (box[2] < r[0] or box[0] > r[2] or
                    box[3] < r[1] or box[1] > r[3]):
                return True
        return False

    def find_site(self, plan, center, face_to, min_r, max_r, tries=12,
                  avoid_players=True):
        """Търси равно, сухо и празно място. -> (origin, rot, found_min)."""
        cx, cz = center
        pos = self.positions() if avoid_players else {}
        for _ in range(tries):
            ang = random.uniform(0, math.tau)
            r = random.uniform(min_r, max_r)
            sx, sz = int(cx + math.cos(ang) * r), int(cz + math.sin(ang) * r)
            fx, fz = face_to
            rot = B.facing_rot(fx - sx, fz - sz)
            lc = B.rot_xz(plan.w // 2, plan.d // 2, rot)
            ox, oz = sx - lc[0], sz - lc[1]
            box = B.bbox(plan, (ox, 0, oz), rot)
            if self._overlaps(B.bbox(plan, (ox, 0, oz), rot, pad=2)):
                continue
            if any(box[0] - 1 <= p[0] <= box[2] + 1 and
                   box[1] - 1 <= p[2] <= box[3] + 1 for p in pos.values()):
                continue
            pts = [(box[0], box[1]), (box[2], box[1]), (box[0], box[3]),
                   (box[2], box[3]), ((box[0] + box[2]) // 2,
                                      (box[1] + box[3]) // 2)]
            ys = world.surfaces(self.s, pts)
            if any(y is None for y in ys):
                continue
            top, low = max(ys), min(ys)
            if top - low > 3 or top < -63 or top > 300:
                continue
            ground = [(x, y, z) for (x, z), y in zip(pts, ys)]
            if any(world.blocks_are(self.s, ground, "minecraft:water")) or \
                    any(world.blocks_are(self.s, ground, "minecraft:lava")):
                continue
            for c in world.clear_vegetation_cmds(box[0], low + 1, box[1],
                                                 box[2], top + 2, box[3]):
                self.s.query(c)
            if not world.region_empty(self.s, box[0], top + 1, box[1],
                                      box[2], top + plan.h, box[3]):
                continue
            return (ox, top, oz), rot, low
        return None

    # ---------- задачи ----------

    def _busy_ids(self):
        return {w["id"] for w in self.workers if w.get("_job")} | \
            set(self.searching)

    def request_build(self, near_name, kind="house", style=None, size=None,
                      owner=None, village=None, worker=None):
        """Строеж до играч. Връща (успех, съобщение) веднага; самото
        търсене на място върви отзад."""
        if not self.cfg.get("workers_enabled", True):
            return False, "Селяните са изключени в настройките."
        pos = self.positions().get(near_name)
        if not pos:
            return False, f"Не виждам {near_name} в света."
        if kind not in B.BUILDERS:
            kind = "house"
        style = style if style in B.STYLES else random.choice(
            ["oak", "spruce", "birch", "stone", "cherry"])
        size = size if size in ("small", "medium", "big") else "medium"
        w = worker
        if w is None:
            free = [w for w in self.workers if w["id"] not in self._busy_ids()]
            w = min(free, key=lambda w: world.distance(w.get("pos"), pos)) \
                if free else None
        if w is None or w["id"] in self._busy_ids():
            w = self.new_worker((pos[0] + 2, pos[1], pos[2] + 2),
                                village=village,
                                prof=PROFESSIONS.get(kind, "mason"))
        if w is None:
            return False, "Всички селяни са заети. Изчакай малко."
        plan = B.make(kind, style, size, random.randint(1, 10 ** 6))
        self.searching[w["id"]] = plan.label
        threading.Thread(target=self._search_and_start,
                         args=(w, plan, kind, style, size, (pos[0], pos[2]),
                               (pos[0], pos[2]), 7, 18, owner, village),
                         daemon=True).start()
        return True, f"{w['name']} търси място за {plan.label}."

    def request_plan(self, near_name, custom, owner=None):
        """Постройка по чертеж, измислен от AI (вече проверен)."""
        if not self.cfg.get("workers_enabled", True):
            return False, "Селяните са изключени в настройките."
        pos = self.positions().get(near_name)
        if not pos:
            return False, f"Не виждам {near_name} в света."
        free = [w for w in self.workers if w["id"] not in self._busy_ids()]
        w = min(free, key=lambda w: world.distance(w.get("pos"), pos)) \
            if free else self.new_worker((pos[0] + 2, pos[1], pos[2] + 2))
        if w is None:
            return False, "Всички селяни са заети."
        job = Job("design", custom=custom)
        self.searching[w["id"]] = job.label
        threading.Thread(target=self._search_and_start,
                         args=(w, job.plan, "design", "oak", "medium",
                               (pos[0], pos[2]), (pos[0], pos[2]), 9, 22,
                               owner, None, custom),
                         daemon=True).start()
        return True, f"{w['name']} търси място за {job.label}."

    def _search_and_start(self, w, plan, kind, style, size, center, face_to,
                          rmin, rmax, owner, village, custom=None):
        try:
            site = self.find_site(plan, center, face_to, rmin, rmax)
        except Exception as e:
            site = None
            log.warn("Села", f"Търсенето на място се провали: {e}")
        with self._lock:
            self.searching.pop(w["id"], None)
        if not site:
            world.speak_near(self.s, w["name"], "yellow",
                             "Не намерих равно и празно място наблизо. "
                             "Пробвай на по-открито.", tuple(w["pos"]), 64)
            log.info("Села", f"{w['name']}: няма място за {plan.label}.")
            return
        origin, rot, low = site
        job = Job(kind, style, size, origin, rot,
                  getattr(plan, "seed", 0), 0, low, village, owner,
                  plan.label, custom)
        box = B.bbox(plan, origin, rot)
        with self._lock:
            self.reserved.append(box)
            w["_job"] = job
            w["box"] = list(box)
        door = self._door_world(job)
        self._spawn(w, (door[0] + 0.5, origin[1] + 1, door[2] + 0.5))
        world.speak_near(self.s, w["name"], "yellow",
                         random.choice(START_LINES), tuple(w["pos"]))
        log.ok("Села", f"{w['name']} почна {plan.label} при "
                       f"X {origin[0]} Z {origin[2]}.")
        self._dirty = True

    def _door_world(self, job):
        dx, dz = job.plan.door
        rx, rz = B.rot_xz(dx, dz + 1, job.rot)
        return (job.origin[0] + rx, job.origin[1], job.origin[2] + rz)

    def found_village(self, near_name):
        """Ново село близо до играча."""
        if not self.cfg.get("workers_enabled", True):
            return False, "Селяните са изключени в настройките."
        if len(self.villages) >= MAX_VILLAGES:
            return False, f"Вече има {len(self.villages)} села — стигат."
        pos = self.positions().get(near_name)
        if not pos:
            return False, f"Не виждам {near_name} в света."
        for v in self.villages:
            c = v["center"]
            if math.hypot(c[0] - pos[0], c[2] - pos[2]) < 80:
                return False, f"Наблизо вече има село — {v['name']}."
        w = self.new_worker((pos[0] + 2, pos[1], pos[2] + 2), prof="mason")
        if not w:
            return False, "Всички селяни са заети."
        plan = B.make("plaza", "oak", "medium", 0)
        self.searching[w["id"]] = "ново село"
        threading.Thread(target=self._found, args=(w, plan, pos),
                         daemon=True).start()
        return True, f"{w['name']} търси място за ново село."

    def _found(self, w, plan, pos):
        try:
            site = self.find_site(plan, (pos[0], pos[2]), (pos[0], pos[2]),
                                  16, 30)
        except Exception as e:
            site = None
            log.warn("Села", f"Търсенето на място се провали: {e}")
        with self._lock:
            self.searching.pop(w["id"], None)
        if not site:
            world.speak_near(self.s, w["name"], "yellow",
                             "Тук е много стръмно или гъсто. Да опитаме "
                             "на поляна.", tuple(w["pos"]), 64)
            return
        origin, rot, low = site
        used = {v["name"] for v in self.villages}
        name = random.choice([n for n in VILLAGE_NAMES if n not in used]
                             or VILLAGE_NAMES)
        lc = B.rot_xz(plan.w // 2, plan.d // 2, rot)
        center = (origin[0] + lc[0], origin[1], origin[2] + lc[1])
        style = random.choice(["oak", "spruce", "birch", "cherry", "stone"])
        with self._lock:
            v = {"id": self._new_id(), "name": name, "center": list(center),
                 "style": style, "made": time.time(), "buildings": [],
                 "plan_idx": 1, "next_at": time.time() + 30}
            self.villages.append(v)
            w["village"] = v["id"]
            job = Job("plaza", style, "medium", origin, rot, 0, 0, low,
                      v["id"], None, f"площада на {name}")
            box = B.bbox(plan, origin, rot)
            self.reserved.append(box)
            w["_job"] = job
            w["box"] = list(box)
        door = self._door_world(job)
        self._spawn(w, (door[0] + 0.5, origin[1] + 1, door[2] + 0.5))
        world.announce(self.s, f"Ново село: {name}",
                       f"{w['name']} строи площада", "yellow",
                       target=f"@a[x={center[0]},y={center[1]},"
                              f"z={center[2]},distance=..160]")
        log.ok("Села", f"Основано {name} при X {center[0]} Z {center[2]}.")
        self._dirty = True

    def stop_worker(self, wid):
        w = self.worker(wid)
        if not w:
            return False
        with self._lock:
            j = w.pop("_job", None)
            if j and w.get("box"):
                box = tuple(w.pop("box"))
                if box in self.reserved:
                    self.reserved.remove(box)
        self._dirty = True
        return bool(j)

    def remove_worker(self, wid):
        w = self.worker(wid)
        if not w:
            return False
        self.stop_worker(wid)
        self.s.send(f"kill @e[tag={self._tag(w)}]")
        self.s.send(f"kill @e[tag={self._tag(w)}_bubble]")
        with self._lock:
            self.workers.remove(w)
        self._dirty = True
        self.save(force=True)
        return True

    # ---------- работа ----------

    def _anyone_near(self, xyz, radius=NEAR_PLAYER):
        for p in self.positions().values():
            if math.hypot(p[0] - xyz[0], p[2] - xyz[2]) < radius:
                return True
        return False

    def tick(self):
        """Вика се на всеки 2 секунди от мозъка."""
        if not self.cfg.get("workers_enabled", True):
            return
        for w in list(self.workers):
            j = w.get("_job")
            if j:
                try:
                    self._work(w, j)
                except Exception as e:
                    log.error("Села", f"{w['name']}: {type(e).__name__}: {e}")
                    w.pop("_job", None)
        self._grow()
        self.save()

    def _work(self, w, j):
        if not self._anyone_near(j.origin):
            return                        # чака някой да дойде
        ops = j.ops
        done_blocks, n = 0, 0
        last = None
        while j.step < len(ops) and n < OPS_PER_TICK and \
                done_blocks < BLOCKS_PER_TICK:
            op = ops[j.step]
            cmds, at = B.to_commands(op, j.origin, j.rot)
            for c in cmds:
                self.s.send(c)
            if at:
                last = at
            done_blocks += B.volume(op)
            n += 1
            j.step += 1
        if last:
            self._stand_near(w, j, last)
            if j.step % 4 == 0:
                x, y, z = last
                self.s.send(f"particle minecraft:happy_villager {x:.1f} "
                            f"{y + 0.5:.1f} {z:.1f} 0.6 0.4 0.6 0 6",
                            optional=True)
                self.s.send(f"playsound minecraft:block.wood.place block @a "
                            f"{x:.1f} {y:.1f} {z:.1f} 0.7 1", optional=True)
        pct = int(j.progress * 100)
        if j.step % 12 == 0 or j.step >= len(ops):
            world.bubble_tag(self.s, self._tag(w), f"{j.label}: {pct}%",
                             2.3, "yellow")
        if j.step >= len(ops):
            self._finish(w, j)
        self._dirty = True

    def _stand_near(self, w, j, at):
        """Застава до мястото, където слага блокове, но извън постройката."""
        box = w.get("box") or B.bbox(j.plan, j.origin, j.rot)
        x, _, z = at
        x1, z1, x2, z2 = box
        cands = [(x, z1 - 1), (x, z2 + 1), (x1 - 1, z), (x2 + 1, z)]
        sx, sz = min(cands, key=lambda c: math.hypot(c[0] - x, c[1] - z))
        y = j.origin[1] + 1
        self.s.send(f"tp @e[tag={self._tag(w)},limit=1] {sx + 0.5:.1f} {y} "
                    f"{sz + 0.5:.1f} facing {x:.1f} {at[1]:.1f} {z:.1f}")
        w["pos"] = [sx + 0.5, y, sz + 0.5]

    def _finish(self, w, j):
        door = self._door_world(j)
        pos = (door[0] + 0.5, j.origin[1] + 1, door[2] + 1.5)
        self.s.send(f"tp @e[tag={self._tag(w)},limit=1] {pos[0]:.1f} "
                    f"{pos[1]} {pos[2]:.1f}")
        w["pos"] = list(pos)
        world.speak_near(self.s, w["name"], "yellow",
                         random.choice(DONE_LINES), pos)
        self.s.send(f"kill @e[tag={self._tag(w)}_bubble]")
        world.firework(self.s, f"@e[tag={self._tag(w)},limit=1]")
        self._free(w)
        v = self.village(j.village)
        with self._lock:
            w.pop("_job", None)
            w["built"] = w.get("built", 0) + 1
            w["idle_since"] = time.time()
            if v is not None:
                v["buildings"].append({"kind": j.kind, "label": j.label,
                                       "origin": list(j.origin),
                                       "rot": j.rot, "door": list(door),
                                       "bbox": w.get("box")})
                v["next_at"] = time.time() + random.uniform(60, 150)
            w.pop("box", None)
        if v is not None and j.kind != "plaza":
            self._path(v, door)
        log.ok("Села", f"{w['name']} завърши {j.label}.")
        self._dirty = True
        self.save(force=True)

    def _path(self, v, door):
        """Пътека от вратата до площада, по самата земя."""
        cx, _, cz = v["center"]
        x0, _, z0 = door
        steps = int(max(abs(cx - x0), abs(cz - z0)))
        for i in range(steps):
            t = i / max(1, steps)
            x = round(x0 + (cx - x0) * t)
            z = round(z0 + (cz - z0) * t)
            if math.hypot(x - cx, z - cz) < 4.5:
                break
            self.s.send(
                f"execute positioned {x} 0 {z} positioned over "
                f"motion_blocking_no_leaves run fill ~ ~-1 ~ ~ ~-1 ~ "
                f"minecraft:dirt_path replace minecraft:grass_block",
                optional=True)

    def village(self, vid):
        return next((v for v in self.villages if v["id"] == vid), None)

    def _grow(self):
        """Селата растат сами, ако някой е наблизо да ги гледа."""
        if not self.cfg.get("village_auto", True):
            return
        now = time.time()
        for v in self.villages:
            if now < v.get("next_at", 0):
                continue
            if v.get("plan_idx", 0) >= min(len(VILLAGE_PLAN), MAX_BUILDINGS):
                continue
            if not self._anyone_near(v["center"], 96):
                continue
            mine = [w for w in self.workers if w.get("village") == v["id"]]
            busy = [w for w in mine if w.get("_job")
                    or w["id"] in self.searching]
            if busy:
                continue
            kind = VILLAGE_PLAN[v["plan_idx"]]
            v["plan_idx"] += 1
            v["next_at"] = now + 45
            free = [w for w in mine if w["id"] not in self._busy_ids()]
            if free:
                w = free[0]
            else:
                w = self.new_worker(tuple(v["center"]), v["id"],
                                    PROFESSIONS.get(kind, "mason"))
            if not w:
                continue
            if len(mine) < 2 and v["plan_idx"] > 3:
                self.new_worker(tuple(v["center"]), v["id"], "farmer")
            size = random.choice(["small", "medium", "medium", "big"]) \
                if kind == "house" else "medium"
            style = v.get("style", "oak") if random.random() < 0.7 else \
                random.choice(list(B.STYLES))
            plan = B.make(kind, style, size, random.randint(1, 10 ** 6))
            self.searching[w["id"]] = plan.label
            c = v["center"]
            rmin, rmax = (6, 9) if kind == "lamp" else (11, 22)
            threading.Thread(target=self._search_and_start,
                             args=(w, plan, kind, style, size, (c[0], c[2]),
                                   (c[0], c[2]), rmin, rmax, None, v["id"]),
                             daemon=True).start()
            self._dirty = True

    def resume(self):
        """След пускане на сървъра: селяните на местата си."""
        for w in self.workers:
            pos = w.get("pos")
            if pos:
                self._spawn(w, tuple(pos))
                if not w.get("_job"):
                    self._free(w)

    def refresh_positions(self):
        """Свободните селяни се движат — питаме къде са."""
        ws = [w for w in self.workers if w.get("free")]
        if not ws:
            return
        res = self.s.query_many([f"data get entity @e[tag={self._tag(w)},"
                                 f"limit=1] Pos" for w in ws])
        for w, (ok, r) in zip(ws, res):
            m = world.POS_RE.search(r or "") if ok else None
            if m:
                w["pos"] = [round(float(v), 1) for v in m.groups()]
            elif ok and "no entity" in (r or "").lower() and w.get("pos"):
                self._spawn(w, tuple(w["pos"]))     # изчезнал — обратно
                self._free(w)

    def giver_positions(self):
        return {f"w{w['id']}": tuple(w["pos"]) for w in self.workers
                if w.get("pos")}

    # ---------- сами ----------

    STAY = 120          # толкова сек играчът стои наоколо, за да стане село
    _anchor = None
    next_auto = 0.0

    def auto_found(self, dims=None):
        """Ново село само, щом някой се е задържал далеч от селата."""
        if not (self.cfg.get("workers_enabled", True) and
                self.cfg.get("village_auto", True)):
            return
        now = time.time()
        if now < self.next_auto:
            return
        self.next_auto = now + 20
        if self._anchor is None:
            self._anchor = {}
        if len(self.villages) >= MAX_VILLAGES or \
                "ново село" in self.searching.values():
            return
        for name, p in self.positions().items():
            if (dims or {}).get(name, "overworld") not in ("overworld",
                                                             "minecraft:overworld"):
                continue
            if any(math.hypot(v["center"][0] - p[0], v["center"][2] - p[2])
                   < 140 for v in self.villages):
                self._anchor.pop(name, None)
                continue
            a = self._anchor.get(name)
            if not a or math.hypot(a[0][0] - p[0], a[0][2] - p[2]) > 60:
                self._anchor[name] = (p, now)
                continue
            if now - a[1] >= self.STAY:
                ok, msg = self.found_village(name)
                if ok:
                    log.ok("Села", f"Ново село до {name} — само. {msg}")
                    self.next_auto = now + 300
                    self._anchor.clear()
                    return

    QUEST_EVERY = 360      # сек между две задачи за един и същ играч
    _q_player = None
    _q_worker = None

    def offer_quests(self, quests, dims=None):
        """Свободен селянин до играч — понякога иска нещо."""
        if not self.cfg.get("villager_quests", True):
            return
        from .quests import VILLAGER_WANTS, ASK_LINES
        if self._q_player is None:
            self._q_player, self._q_worker = {}, {}
        now = time.time()
        players = {n: p for n, p in self.positions().items()
                   if (dims or {}).get(n, "overworld") == "overworld"}
        for w in self.workers:
            if w.get("_job") or not w.get("pos") or w["id"] in self.searching:
                continue
            if now - self._q_worker.get(w["id"], 0) < 180:
                continue
            for name, p in players.items():
                if world.distance(p, w["pos"]) > 7:
                    continue
                if now - self._q_player.get(name, 0) < self.QUEST_EVERY:
                    continue
                if len(quests.active_for(name)) >= 2:
                    continue
                pool = VILLAGER_WANTS.get(w.get("prof"), []) + \
                    VILLAGER_WANTS["*"]
                kind, target, amount, reward = random.choice(pool)
                at = None
                if kind == "visit":
                    others = [v for v in self.villages
                              if v["id"] != w.get("village")]
                    if others:
                        c = random.choice(others)["center"]
                        at = (int(c[0]), int(c[2]))
                    else:
                        ang = random.uniform(0, math.tau)
                        at = (int(p[0] + math.cos(ang) * 120),
                              int(p[2] + math.sin(ang) * 120))
                giver = {"name": w["name"], "color": "yellow",
                         "kind": "villager", "ref": f"w{w['id']}"}
                ok, label = quests.give(name, kind, target, amount,
                                        reward=reward, fame=12, giver=giver,
                                        minutes=40, at=at)
                self._q_player[name] = now
                self._q_worker[w["id"]] = now
                if ok:
                    world.speak_near(self.s, w["name"], "yellow",
                                     random.choice(ASK_LINES).format(
                                         p=name, task=label.lower()),
                                     tuple(w["pos"]), 24)
                break

    # ---------- за режисьора и интерфейса ----------

    def status(self):
        out = {"villages": [], "workers": []}
        for v in self.villages:
            out["villages"].append({
                "id": v["id"], "name": v["name"],
                "x": int(v["center"][0]), "z": int(v["center"][2]),
                "buildings": len(v.get("buildings", [])),
                "kinds": [b["kind"] for b in v.get("buildings", [])]})
        for w in self.workers:
            j = w.get("_job")
            v = self.village(w.get("village"))
            out["workers"].append({
                "id": w["id"], "name": w["name"],
                "village": v["name"] if v else None,
                "job": j.label if j else None,
                "progress": round(j.progress, 2) if j else None,
                "searching": self.searching.get(w["id"]),
                "waiting": bool(j) and not self._anyone_near(j.origin),
                "built": w.get("built", 0),
                "x": int(w["pos"][0]) if w.get("pos") else None,
                "z": int(w["pos"][2]) if w.get("pos") else None})
        return out

    def summary(self):
        """Кратко за промпта на режисьора."""
        lines = []
        for v in self.villages:
            kinds = [B.KIND_NAMES.get(b["kind"], b["kind"])
                     for b in v.get("buildings", [])]
            lines.append(f"село {v['name']} при X={int(v['center'][0])} "
                         f"Z={int(v['center'][2])}: "
                         f"{', '.join(kinds) or 'тепърва се строи'}")
        for w in self.workers:
            j = w.get("_job")
            if j:
                lines.append(f"{w['name']} строи {j.label} "
                             f"({int(j.progress * 100)}%)")
            elif w["id"] in self.searching:
                lines.append(f"{w['name']} търси място")
        return lines

