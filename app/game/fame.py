"""
fame.py — слава и рангове.

Всеки играч трупа „слава" за мисии, победи, босове и постижения. Тя се
вижда в TAB до името, а рангът — пред името в чата: [Новак] → [Странник]
→ [Герой] → [Шампион] → [Легенда]. При нов ранг целият сървър разбира.

Ранговете са отбори (team) с представка. Конзолата после пише
„[Герой] Mia joined the game" — events.py маха представката, преди да
разчита реда.
"""

import time

from ..logbus import log
from . import world

OBJ = "slava"
RANKS = [(0, "Новак", "gray"), (50, "Странник", "green"),
         (150, "Герой", "aqua"), (400, "Шампион", "gold"),
         (1000, "Легенда", "light_purple")]


def rank_of(points):
    idx = 0
    for i, (need, _n, _c) in enumerate(RANKS):
        if points >= need:
            idx = i
    return idx


class Fame:
    def __init__(self, sender, brain):
        self.s = sender
        self.b = brain
        self._ready = False

    def setup(self):
        """Таблото в TAB и отборите-рангове. Безопасно е да се вика пак."""
        cmds = [f'scoreboard objectives add {OBJ} dummy '
                f'{{"text":"Слава","color":"gold"}}',
                f"scoreboard objectives setdisplay list {OBJ}"]
        for i, (_need, name, col) in enumerate(RANKS):
            t = f"bvg_r{i}"
            cmds += [f"team add {t}",
                     f'team modify {t} prefix {{"text":"[{name}] ",'
                     f'"color":"{col}"}}',
                     f"team modify {t} color {col}"]
        self.s.send_many(cmds)
        self._ready = True
        for n in list(self.b.server.online):
            self.sync(n)

    def points(self, name):
        return int(self.b.player(name).get("fame", 0))

    def sync(self, name):
        """Точките и рангът на играча в играта."""
        if not world.name_ok(name):
            return
        pts = self.points(name)
        self.s.send(f"scoreboard players set {name} {OBJ} {pts}")
        self.s.send(f"team join bvg_r{rank_of(pts)} {name}")

    def add(self, name, pts, reason=""):
        if not world.name_ok(name) or not pts:
            return
        info = self.b.player(name)
        before = int(info.get("fame", 0))
        after = max(0, before + int(pts))
        info["fame"] = after
        info.setdefault("fame_log", []).append(
            [int(time.time()), int(pts), str(reason)[:60]])
        info["fame_log"] = info["fame_log"][-30:]
        self.b._save_players()
        if pts > 0:
            world.actionbar(self.s, name, f"+{pts} слава" +
                            (f" · {reason}" if reason else ""), "gold")
        self.sync(name)
        r0, r1 = rank_of(before), rank_of(after)
        if r1 > r0:
            _need, rname, col = RANKS[r1]
            world.announce(self.s, f"{name} е {rname}!",
                           "нов ранг", col)
            world.firework(self.s, name)
            world.say(self.s, "keeper", f"Нов ранг! {name} вече е "
                                        f"{rname}. Светът помни.")
            log.ok("Слава", f"{name} стана {rname} ({after})")
            try:
                self.b.director.note(f"{name} стана {rname}", important=True)
            except Exception:
                pass

    def top(self, n=5):
        rows = [(name, int(info.get("fame", 0)))
                for name, info in self.b.players.items()
                if info.get("fame")]
        rows.sort(key=lambda r: -r[1])
        return [{"name": name, "fame": pts, "rank": RANKS[rank_of(pts)][1],
                 "color": RANKS[rank_of(pts)][2]} for name, pts in rows[:n]]
