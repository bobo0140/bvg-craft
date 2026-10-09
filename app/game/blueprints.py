"""
blueprints.py — чертежите на постройките.

Всеки чертеж е списък от стъпки в ред, в който един строител би ги
направил: основа, стени ред по ред, прозорци, врата, таван, покрив,
обзавеждане. Координатите са местни: (0,0,0) е ъгълът на пода, x е
ширината, z — дълбочината, а лицето (вратата) гледа към +z (юг).
При строеж всичко се завърта според посоката към селото или играча.

Стъпки:
  ("F", x1,y1,z1, x2,y2,z2, блок)            fill
  ("R", x1,y1,z1, x2,y2,z2, блок, филтър)    fill ... replace филтър
  ("S", x,y,z, блок)                         setblock
  ("T", x,y,z, дърво)                        place feature (дърво)
"""

import random
import re

# ---------- стилове ----------

STYLES = {
    "oak": dict(floor="oak_planks", wall="oak_planks",
                frame="oak_log[axis=y]", beam="stripped_oak_log",
                roof="oak_stairs", roof_full="oak_planks", slab="oak_slab",
                door="oak_door", fence="oak_fence", found="cobblestone",
                bed="red_bed", carpet="red_carpet"),
    "spruce": dict(floor="spruce_planks", wall="spruce_planks",
                   frame="spruce_log[axis=y]", beam="stripped_spruce_log",
                   roof="dark_oak_stairs", roof_full="dark_oak_planks",
                   slab="dark_oak_slab", door="spruce_door",
                   fence="spruce_fence", found="cobblestone",
                   bed="blue_bed", carpet="blue_carpet"),
    "birch": dict(floor="birch_planks", wall="birch_planks",
                  frame="birch_log[axis=y]", beam="stripped_birch_log",
                  roof="spruce_stairs", roof_full="spruce_planks",
                  slab="spruce_slab", door="birch_door", fence="birch_fence",
                  found="stone_bricks", bed="yellow_bed",
                  carpet="yellow_carpet"),
    "stone": dict(floor="stone_bricks", wall="stone_bricks",
                  frame="polished_andesite", beam="spruce_log",
                  roof="deepslate_tile_stairs", roof_full="deepslate_tiles",
                  slab="deepslate_tile_slab", door="spruce_door",
                  fence="spruce_fence", found="stone_bricks",
                  bed="gray_bed", carpet="gray_carpet"),
    "cherry": dict(floor="cherry_planks", wall="white_terracotta",
                   frame="cherry_log[axis=y]", beam="stripped_cherry_log",
                   roof="cherry_stairs", roof_full="cherry_planks",
                   slab="cherry_slab", door="cherry_door",
                   fence="cherry_fence", found="stone_bricks",
                   bed="pink_bed", carpet="pink_carpet"),
    "desert": dict(floor="smooth_sandstone", wall="sandstone",
                   frame="cut_sandstone", beam="stripped_jungle_log",
                   roof="smooth_sandstone_stairs",
                   roof_full="smooth_sandstone", slab="smooth_sandstone_slab",
                   door="jungle_door", fence="jungle_fence",
                   found="sandstone", bed="orange_bed",
                   carpet="orange_carpet"),
    "mud": dict(floor="packed_mud", wall="mud_bricks",
                frame="mangrove_log[axis=y]", beam="stripped_mangrove_log",
                roof="mangrove_stairs", roof_full="mangrove_planks",
                slab="mangrove_slab", door="mangrove_door",
                fence="mangrove_fence", found="mud_bricks",
                bed="brown_bed", carpet="brown_carpet"),
}

STYLE_NAMES = {"oak": "дъб", "spruce": "смърч", "birch": "бреза",
               "stone": "камък", "cherry": "череша", "desert": "пясък",
               "mud": "кал"}

KIND_NAMES = {"house": "къща", "farm": "ферма", "well": "кладенец",
              "tower": "кула", "garden": "градина", "lamp": "фенер",
              "stall": "сергия", "plaza": "площад"}

# Думи, с които хората искат постройка
KIND_WORDS = {
    "house": ("къщ", "дом", "колиб", "house", "home"),
    "farm": ("ферм", "нив", "градинк", "farm", "жито", "морков"),
    "well": ("кладен", "чешм", "well"),
    "tower": ("кул", "tower", "наблюд"),
    "garden": ("градин", "парк", "цвет", "дърв", "garden"),
    "lamp": ("фенер", "лампа", "осветл", "lamp"),
    "stall": ("сергия", "пазар", "магазин", "stall", "market"),
}


def kind_from_text(text):
    low = (text or "").lower()
    for kind, words in KIND_WORDS.items():
        if any(w in low for w in words):
            return kind
    return None


def style_from_text(text):
    low = (text or "").lower()
    for key, name in STYLE_NAMES.items():
        if key in low or name[:4] in low:
            return key
    return None


class Plan:
    def __init__(self, kind, w, d, h, ops, label, door=None):
        self.kind, self.w, self.d, self.h = kind, w, d, h
        self.ops, self.label = ops, label
        self.door = door if door is not None else ((w - 1) // 2, d)


# ---------- помощни ----------

def _outline(ops, y, w, d, block):
    """Една редица стени по периметъра."""
    ops.append(("F", 0, y, 0, w - 1, y, 0, block))
    ops.append(("F", 0, y, d - 1, w - 1, y, d - 1, block))
    if d > 2:
        ops.append(("F", 0, y, 1, 0, y, d - 2, block))
        ops.append(("F", w - 1, y, 1, w - 1, y, d - 2, block))


def _fence_ring(ops, y, w, d, fence):
    """Ограда с правилните връзки — иначе изглежда като стълбчета."""
    ew = f"{fence}[east=true,west=true]"
    ns = f"{fence}[north=true,south=true]"
    ops.append(("F", 1, y, 0, w - 2, y, 0, ew))
    ops.append(("F", 1, y, d - 1, w - 2, y, d - 1, ew))
    ops.append(("F", 0, y, 1, 0, y, d - 2, ns))
    ops.append(("F", w - 1, y, 1, w - 1, y, d - 2, ns))
    ops.append(("S", 0, y, 0, f"{fence}[east=true,south=true]"))
    ops.append(("S", w - 1, y, 0, f"{fence}[west=true,south=true]"))
    ops.append(("S", 0, y, d - 1, f"{fence}[east=true,north=true]"))
    ops.append(("S", w - 1, y, d - 1, f"{fence}[west=true,north=true]"))


def _pane(axis):
    return ("glass_pane[east=true,west=true]" if axis == "x"
            else "glass_pane[north=true,south=true]")


FLOWERS = ["poppy", "dandelion", "cornflower", "allium", "azure_bluet",
           "oxeye_daisy", "red_tulip", "orange_tulip", "pink_tulip",
           "lily_of_the_valley", "blue_orchid"]


# ---------- чертежи ----------

def house(style="oak", size="medium", seed=0):
    P = STYLES.get(style, STYLES["oak"])
    w, d, H = {"small": (5, 5, 4), "medium": (7, 6, 4),
               "big": (9, 7, 5)}.get(size, (7, 6, 4))
    c = (w - 1) // 2
    ops = []
    # основа и под
    ops.append(("F", 0, 0, 0, w - 1, 0, d - 1, P["found"]))
    ops.append(("F", 1, 0, 1, w - 2, 0, d - 2, P["floor"]))
    # стени ред по ред, с колони по ъглите
    for y in range(1, H + 1):
        _outline(ops, y, w, d, P["wall"])
        for x, z in ((0, 0), (w - 1, 0), (0, d - 1), (w - 1, d - 1)):
            ops.append(("S", x, y, z, P["frame"]))
    # прозорци: отзад, отстрани и отпред (не в средата отзад — там е факлата)
    wy2 = 3 if H >= 4 else 2
    back = {5: [1, 3], 7: [2, 4], 9: [2, 6]}.get(w, [1, w - 2])
    for x in back:
        ops.append(("F", x, 2, 0, x, wy2, 0, _pane("x")))
    side = [(d - 1) // 2] if d < 7 else [2, d - 3]
    for z in side:
        ops.append(("F", 0, 2, z, 0, wy2, z, _pane("z")))
        ops.append(("F", w - 1, 2, z, w - 1, wy2, z, _pane("z")))
    if w >= 7:
        for x in (c - 2, c + 2):
            ops.append(("F", x, 2, d - 1, x, wy2, d - 1, _pane("x")))
    # врата
    ops.append(("S", c, 1, d - 1,
                f"{P['door']}[facing=north,half=lower,hinge=left]"))
    ops.append(("S", c, 2, d - 1,
                f"{P['door']}[facing=north,half=upper,hinge=left]"))
    # таван
    ops.append(("F", 1, H + 1, 1, w - 2, H + 1, d - 2, P["floor"]))
    # покрив на две води, билото е по x
    stairs = P["roof"]
    i = 0
    while True:
        zn, zs, y = i - 1, d - i, H + 1 + i
        if zs < zn:
            break
        if zs == zn:
            ops.append(("F", -1, y, zn, w, y, zn,
                        f"{P['slab']}[type=bottom]"))
            break
        ops.append(("F", -1, y, zn, w, y, zn,
                    f"{stairs}[facing=south,half=bottom]"))
        ops.append(("F", -1, y, zs, w, y, zs,
                    f"{stairs}[facing=north,half=bottom]"))
        if i >= 1:
            ops.append(("F", 0, y - 1, zn, w - 1, y - 1, zn, P["roof_full"]))
            ops.append(("F", 0, y - 1, zs, w - 1, y - 1, zs, P["roof_full"]))
        if zs - zn == 2:
            ops.append(("F", -1, y, zn + 1, w, y, zn + 1, P["roof_full"]))
            ops.append(("F", -1, y + 1, zn + 1, w, y + 1, zn + 1,
                        f"{P['slab']}[type=bottom]"))
            break
        if zs - zn > 2:
            ops.append(("F", 0, y, zn + 1, 0, y, zs - 1, P["wall"]))
            ops.append(("F", w - 1, y, zn + 1, w - 1, y, zs - 1, P["wall"]))
        else:
            break
        i += 1
    roof_h = H + 2 + i
    # обзавеждане
    ops.append(("S", 1, 1, 1, f"{P['bed']}[facing=north,part=head]"))
    ops.append(("S", 1, 1, 2, f"{P['bed']}[facing=north,part=foot]"))
    ops.append(("S", w - 2, 1, 1, "crafting_table"))
    ops.append(("S", w - 2, 1, 2, "chest[facing=west]"))
    if d >= 6:
        ops.append(("S", w - 2, 1, 3, "furnace[facing=west]"))
    if w >= 7:
        ops.append(("S", c, 1, (d - 1) // 2, P["carpet"]))
        ops.append(("S", c + 1, 1, (d - 1) // 2, P["carpet"]))
    ops.append(("S", c, 3, 1, "wall_torch[facing=south]"))
    ops.append(("S", c, H, (d - 1) // 2, "lantern[hanging=true]"))
    # отвън: факли до вратата и пътека
    ops.append(("S", c - 1, 3, d, "wall_torch[facing=south]"))
    ops.append(("S", c + 1, 3, d, "wall_torch[facing=south]"))
    ops.append(("R", c, 0, d, c, 0, d + 2, "dirt_path", "grass_block"))
    rnd = random.Random(seed)
    for x in (c - 2, c + 2):
        if 0 < x < w - 1:
            ops.append(("S", x, 1, d, "potted_" + rnd.choice(
                ["poppy", "dandelion", "cornflower", "red_tulip"])))
    names = {"small": "малка къща", "medium": "къща", "big": "голяма къща"}
    return Plan("house", w, d, roof_h, ops,
                f"{names.get(size, 'къща')} ({STYLE_NAMES.get(style, style)})")


def farm(style="oak", size="medium", seed=0):
    rnd = random.Random(seed)
    w, d = (9, 7) if size != "big" else (11, 9)
    mid = d // 2
    fence = STYLES.get(style, STYLES["oak"])["fence"]
    crops = rnd.sample(["wheat[age=7]", "carrots[age=7]", "potatoes[age=7]",
                        "beetroots[age=3]"], 2)
    ops = [("F", 0, 0, 0, w - 1, 0, d - 1, "spruce_planks"),
           ("F", 1, 0, 1, w - 2, 0, d - 2, "farmland[moisture=7]"),
           ("F", 1, 0, mid, w - 2, 0, mid, "water"),
           ("F", 1, 1, 1, w - 2, 1, mid - 1, crops[0]),
           ("F", 1, 1, mid + 1, w - 2, 1, d - 2, crops[1])]
    _fence_ring(ops, 1, w, d, fence)
    ops.append(("S", (w - 1) // 2, 1, d - 1,
                f"{fence}_gate[facing=south]"))
    for x, z in ((0, 0), (w - 1, 0), (0, d - 1), (w - 1, d - 1)):
        ops.append(("S", x, 2, z, "lantern"))
    for x in range(2, w - 2, 3):
        ops.append(("S", x, 1, mid, "lily_pad"))
    return Plan("farm", w, d, 3, ops, "ферма")


def well(style="oak", size="medium", seed=0):
    fence = STYLES.get(style, STYLES["oak"])["fence"]
    slab = STYLES.get(style, STYLES["oak"])["slab"]
    ops = [("F", 0, 0, 0, 4, 0, 4, "cobblestone"),
           ("F", 1, -3, 1, 3, -3, 3, "cobblestone"),
           ("F", 1, -2, 1, 3, 1, 3, "mossy_stone_bricks"),
           ("F", 2, -2, 2, 2, 0, 2, "water"),
           ("S", 2, 1, 2, "air")]
    for x, z in ((1, 1), (3, 1), (1, 3), (3, 3)):
        ops.append(("F", x, 2, z, x, 3, z, fence))
    ops.append(("F", 1, 4, 1, 3, 4, 3, f"{slab}[type=bottom]"))
    ops.append(("S", 2, 3, 2, "lantern[hanging=true]"))
    return Plan("well", 5, 5, 5, ops, "кладенец", door=(2, 5))


def tower(style="stone", size="medium", seed=0):
    H = {"small": 8, "medium": 11, "big": 15}.get(size, 11)
    wall = "stone_bricks"
    ops = [("F", 0, 0, 0, 4, 0, 4, "stone_bricks")]
    for y in range(1, H + 1):
        _outline(ops, y, 5, 5, "mossy_stone_bricks" if y % 4 == 0 else wall)
    ops.append(("S", 2, 1, 4, "spruce_door[facing=north,half=lower,hinge=left]"))
    ops.append(("S", 2, 2, 4, "spruce_door[facing=north,half=upper,hinge=left]"))
    for y in range(4, H, 3):
        ops.append(("S", 0, y, 2, _pane("z")))
        ops.append(("S", 4, y, 2, _pane("z")))
        ops.append(("S", 3, y + 1, 0, _pane("x")))   # не до стълбата
    ops.append(("F", 0, H + 1, 0, 4, H + 1, 4, "stone_bricks"))
    ops.append(("F", 2, 1, 1, 2, H + 1, 1, "ladder[facing=south]"))
    for x in range(5):
        for z in range(5):
            if (x in (0, 4) or z in (0, 4)) and (x + z) % 2 == 0:
                ops.append(("S", x, H + 2, z, "stone_bricks"))
    for x, z in ((1, 3), (3, 3), (3, 1)):
        ops.append(("S", x, H + 2, z, "lantern"))
    ops.append(("S", 2, 3, 3, "wall_torch[facing=north]"))
    return Plan("tower", 5, 5, H + 3, ops, "кула")


def garden(style="oak", size="medium", seed=0):
    rnd = random.Random(seed)
    w = d = 9 if size == "big" else 7
    c = w // 2
    ops = [("F", 0, 0, 0, w - 1, 0, d - 1, "grass_block"),
           ("F", c, 0, 0, c, 0, d - 1, "gravel"),
           ("F", 0, 0, c, w - 1, 0, c, "gravel")]
    hedge = "oak_leaves[persistent=true]"
    ops.append(("F", 0, 1, 0, w - 1, 1, 0, hedge))
    ops.append(("F", 0, 1, d - 1, w - 1, 1, d - 1, hedge))
    ops.append(("F", 0, 1, 1, 0, 1, d - 2, hedge))
    ops.append(("F", w - 1, 1, 1, w - 1, 1, d - 2, hedge))
    for x, z in ((c, 0), (c, d - 1), (0, c), (w - 1, c)):
        ops.append(("S", x, 1, z, "air"))
    quads = [(1, 1), (c + 1, 1), (1, c + 1), (c + 1, c + 1)]
    trees = rnd.sample(quads, 2)
    for qx, qz in quads:
        for dx in range(c - 1):
            for dz in range(c - 1):
                x, z = qx + dx, qz + dz
                if (qx, qz) in trees and (dx, dz) == ((c - 2) // 2,
                                                      (c - 2) // 2):
                    continue
                if rnd.random() < 0.65:
                    ops.append(("S", x, 1, z, rnd.choice(FLOWERS)))
    for qx, qz in trees:
        ops.append(("T", qx + (c - 2) // 2, 1, qz + (c - 2) // 2,
                    rnd.choice(["birch", "oak", "cherry", "azalea_tree"])))
    ops.append(("S", c, 1, c, STYLES.get(style, STYLES["oak"])["fence"]))
    ops.append(("S", c, 2, c, "lantern"))
    return Plan("garden", w, d, 9, ops, "градина", door=(c, d))


def lamp(style="oak", size="medium", seed=0):
    fence = STYLES.get(style, STYLES["oak"])["fence"]
    ops = [("S", 0, 0, 0, "cobblestone"),
           ("F", 0, 1, 0, 0, 3, 0, fence),
           ("S", 0, 4, 0, "lantern")]
    return Plan("lamp", 1, 1, 5, ops, "фенер", door=(0, 1))


def stall(style="oak", size="medium", seed=0):
    rnd = random.Random(seed)
    P = STYLES.get(style, STYLES["oak"])
    wool = rnd.choice([("red_wool", "white_wool"), ("blue_wool", "white_wool"),
                       ("green_wool", "yellow_wool")])
    ops = [("F", 0, 0, 0, 4, 0, 2, P["floor"])]
    for x, z in ((0, 0), (4, 0), (0, 2), (4, 2)):
        ops.append(("F", x, 1, z, x, 2, z, P["fence"]))
    for z in range(3):
        ops.append(("F", 0, 3, z, 4, 3, z, wool[z % 2]))
    ops.append(("S", 1, 1, 0, "barrel[facing=up]"))
    ops.append(("S", 3, 1, 0, "barrel[facing=up]"))
    ops.append(("F", 1, 1, 2, 3, 1, 2, f"{P['slab']}[type=top]"))
    ops.append(("S", 2, 1, 1, "composter"))
    ops.append(("S", 2, 2, 2, "lantern"))
    return Plan("stall", 5, 3, 4, ops, "сергия", door=(2, 3))


def plaza(style="oak", size="medium", seed=0):
    fence = STYLES.get(style, STYLES["oak"])["fence"]
    ops = [("F", 0, 0, 0, 6, 0, 6, "stone_bricks"),
           ("F", 1, 0, 1, 5, 0, 5, "polished_andesite"),
           ("S", 3, 0, 3, "chiseled_stone_bricks"),
           ("S", 3, 1, 3, "campfire[lit=true]")]
    for x, z in ((0, 0), (6, 0), (0, 6), (6, 6)):
        ops.append(("F", x, 1, z, x, 3, z, fence))
        ops.append(("S", x, 4, z, "lantern"))
    for x, z, f in ((3, 1, "south"), (3, 5, "north"), (1, 3, "east"),
                    (5, 3, "west")):
        ops.append(("S", x, 1, z, f"oak_stairs[facing={f},half=bottom]"))
    return Plan("plaza", 7, 7, 5, ops, "площад", door=(3, 7))


BUILDERS = {"house": house, "farm": farm, "well": well, "tower": tower,
            "garden": garden, "lamp": lamp, "stall": stall, "plaza": plaza}


def make(kind, style="oak", size="medium", seed=0):
    plan = BUILDERS.get(kind, house)(style, size, seed)
    plan.style, plan.size, plan.seed = style, size, seed
    return plan


# ---------- завъртане ----------

CW = {"north": "east", "east": "south", "south": "west", "west": "north"}


def rot_xz(x, z, r):
    """Завърта местни x,z на r пъти по 90° по часовника (гледано отгоре).

    r=0: лицето е на юг (+z); 1: на запад; 2: на север; 3: на изток.
    """
    for _ in range(r % 4):
        x, z = -z, x
    return x, z


def rot_state(block, r):
    """Завърта посоките в състоянието на блока: facing, axis, страните."""
    r %= 4
    if not r or "[" not in block:
        return block
    name, _, props = block.partition("[")
    props = props.rstrip("]")
    out = []
    for kv in props.split(","):
        k, _, v = kv.partition("=")
        if k == "facing" and v in CW:
            for _ in range(r):
                v = CW[v]
        elif k == "axis" and v in ("x", "z") and r % 2:
            v = "z" if v == "x" else "x"
        elif k in CW:
            for _ in range(r):
                k = CW[k]
        out.append(f"{k}={v}")
    return f"{name}[{','.join(out)}]"


def ns(block):
    """minecraft: отпред, ако липсва (без таговете с #)."""
    if block.startswith("#") or ":" in block.split("[")[0]:
        return block
    return "minecraft:" + block


def to_commands(op, origin, r):
    """Една стъпка от чертежа -> команда в света."""
    ox, oy, oz = origin

    def at(x, y, z):
        rx, rz = rot_xz(x, z, r)
        return ox + rx, oy + y, oz + rz

    kind = op[0]
    if kind in ("F", "R"):
        x1, y1, z1 = at(*op[1:4])
        x2, y2, z2 = at(*op[4:7])
        cmd = (f"fill {x1} {y1} {z1} {x2} {y2} {z2} "
               f"{ns(rot_state(op[7], r))}")
        if kind == "R":
            cmd += f" replace {ns(op[8])}"
        return [cmd], ((x1 + x2) / 2, (y1 + y2) / 2, (z1 + z2) / 2)
    if kind == "S":
        x, y, z = at(*op[1:4])
        return [f"setblock {x} {y} {z} {ns(rot_state(op[4], r))}"], (x, y, z)
    if kind == "T":
        x, y, z = at(*op[1:4])
        return [f"place feature minecraft:{op[4]} {x} {y} {z}"], (x, y, z)
    return [], None


def bbox(plan, origin, r, pad=0):
    """Заеманата площ в света (x1, z1, x2, z2), с отстъп pad."""
    ox, _, oz = origin
    pts = [rot_xz(x, z, r) for x, z in ((-1, -1), (plan.w, -1),
                                         (-1, plan.d), (plan.w, plan.d))]
    xs = [ox + p[0] for p in pts]
    zs = [oz + p[1] for p in pts]
    return (min(xs) - pad, min(zs) - pad, max(xs) + pad, max(zs) + pad)


def facing_rot(dx, dz):
    """Кое завъртане обръща лицето (+z) към посоката (dx, dz)."""
    if abs(dz) >= abs(dx):
        return 0 if dz > 0 else 2
    return 3 if dx > 0 else 1


def volume(op):
    if op[0] in ("F", "R"):
        return ((abs(op[4] - op[1]) + 1) * (abs(op[5] - op[2]) + 1)
                * (abs(op[6] - op[3]) + 1))
    return 1


BLOCK_RE = re.compile(r"^#?[a-z0-9_:]+(\[[a-z0-9_=,]+\])?$")


def all_blocks():
    """Всички блокове от всички чертежи и стилове — за проверка в CI."""
    out = set()
    for kind, fn in BUILDERS.items():
        for style in STYLES:
            for size in ("small", "medium", "big"):
                for seed in range(3):
                    for op in fn(style, size, seed).ops:
                        if op[0] in ("F", "R"):
                            out.add(op[7])
                        elif op[0] == "S":
                            out.add(op[4])
    return out
