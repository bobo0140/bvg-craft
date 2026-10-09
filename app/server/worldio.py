"""
worldio.py — внасяне на стар свят или цял стар сървър.

Свят се разпознава по `level.dat`. Четем го (gzip + NBT), за да покажем
от коя версия на Minecraft е: Minecraft сам надгражда стар свят, но
отказва да зареди такъв от по-нова версия.

Оригиналът никога не се пипа — копира се. Текущият свят се премества в
`server/_backups`, а не се трие.
"""

import gzip
import io
import os
import re
import shutil
import struct
import time
import zipfile

from .. import paths
from ..logbus import log
from .manager import ver_tuple

DIMS = ("", "_nether", "_the_end")       # суфикси на папките в стария формат
SKIP = {"session.lock"}                  # заключен е, сървърът го прави наново


# ---------- NBT ----------

class _R:
    def __init__(self, data):
        self.b, self.i = data, 0

    def take(self, n):
        v = self.b[self.i:self.i + n]
        if len(v) < n:
            raise ValueError("повреден NBT")
        self.i += n
        return v

    def u8(self): return self.take(1)[0]
    def i8(self): return struct.unpack(">b", self.take(1))[0]
    def i16(self): return struct.unpack(">h", self.take(2))[0]
    def i32(self): return struct.unpack(">i", self.take(4))[0]
    def i64(self): return struct.unpack(">q", self.take(8))[0]
    def f32(self): return struct.unpack(">f", self.take(4))[0]
    def f64(self): return struct.unpack(">d", self.take(8))[0]

    def string(self):
        n = struct.unpack(">H", self.take(2))[0]
        return self.take(n).decode("utf-8", errors="replace")


def _payload(r, t, depth=0):
    if depth > 64:
        raise ValueError("твърде дълбок NBT")
    if t == 1: return r.i8()
    if t == 2: return r.i16()
    if t == 3: return r.i32()
    if t == 4: return r.i64()
    if t == 5: return r.f32()
    if t == 6: return r.f64()
    if t == 7:
        n = r.i32(); r.take(n); return f"<{n} байта>"
    if t == 8: return r.string()
    if t == 9:
        et, n = r.u8(), r.i32()
        return [_payload(r, et, depth + 1) for _ in range(max(0, n))]
    if t == 10:
        out = {}
        while True:
            tt = r.u8()
            if tt == 0:
                return out
            name = r.string()
            out[name] = _payload(r, tt, depth + 1)
    if t == 11:
        n = r.i32(); r.take(4 * n); return f"<{n} int>"
    if t == 12:
        n = r.i32(); r.take(8 * n); return f"<{n} long>"
    raise ValueError(f"непознат NBT таг {t}")


def parse_nbt(data: bytes) -> dict:
    r = _R(data)
    t = r.u8()
    if t != 10:
        raise ValueError("level.dat не започва с compound")
    r.string()
    return _payload(r, 10)


def read_level_dat(raw: bytes) -> dict:
    """Съдържанието на level.dat -> речник с най-важното."""
    try:
        data = gzip.decompress(raw)
    except OSError:
        data = raw
    root = parse_nbt(data)
    d = root.get("Data", root)
    ver = d.get("Version") or {}
    gen = d.get("WorldGenSettings") or {}
    seed = gen.get("seed", d.get("RandomSeed"))
    return {
        "level_name": d.get("LevelName") or "",
        "version": ver.get("Name") or "",
        "data_version": ver.get("Id") or d.get("DataVersion"),
        "last_played": (d.get("LastPlayed") or 0) / 1000.0,
        "gamemode": d.get("GameType"),
        "hardcore": bool(d.get("hardcore")),
        "seed": seed,
    }


# ---------- намиране на света ----------

def _dir_size(path):
    total = 0
    for root, _d, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _read_properties(path):
    out = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def find_world(path: str):
    """Намира света в избраната папка. Връща речник или None.

    Приема самата папка на света, цял стар сървър или папка, в която
    има свят едно ниво по-надолу.
    """
    path = os.path.abspath(path)
    if os.path.isfile(os.path.join(path, "level.dat")):
        return _describe(path, server_root=None)

    props = _read_properties(os.path.join(path, "server.properties"))
    name = props.get("level-name") or "world"
    cand = os.path.join(path, name)
    if os.path.isfile(os.path.join(cand, "level.dat")):
        return _describe(cand, server_root=path)

    found = _search(path)
    if not found:
        return None
    # Сървърите на Paper/Spigot имат level.dat и в world_nether и
    # world_the_end. Те са измерения на основния свят, не отделни светове.
    main = [f for f in found
            if not any(f.endswith(sfx) and f[:-len(sfx)] in found
                       for sfx in DIMS[1:])] or found
    # Ако има server.properties, той казва кой е светът
    for f in main:
        root = _server_root(f, path)
        if root:
            want = _read_properties(os.path.join(root, "server.properties")
                                    ).get("level-name") or "world"
            if os.path.basename(f) == want:
                return _describe(f, server_root=root,
                                 alternatives=main if len(main) > 1 else [])
    best = max(main, key=_dir_size)
    return _describe(best, server_root=_server_root(best, path),
                     alternatives=main if len(main) > 1 else [])


def _search(path, max_depth=3):
    """Папките с level.dat до няколко нива надолу.

    Архивът на цял сървър обикновено има още една папка отгоре
    (Сървър/world/level.dat), затова едно ниво не стига.
    """
    found = []
    base = path.rstrip(os.sep).count(os.sep)
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in ("__MACOSX", "_backups")]
        if "level.dat" in files:
            found.append(root)
            dirs[:] = []                     # не слизаме в самия свят
        elif root.count(os.sep) - base >= max_depth:
            dirs[:] = []
    return sorted(found)


def _server_root(world_dir, stop):
    """Най-близката папка нагоре със server.properties."""
    cur = os.path.dirname(world_dir)
    stop = os.path.abspath(stop)
    while True:
        if os.path.isfile(os.path.join(cur, "server.properties")):
            return cur
        if os.path.abspath(cur) == stop or os.path.dirname(cur) == cur:
            return None
        cur = os.path.dirname(cur)


def _describe(world_dir, server_root, alternatives=None):
    with open(os.path.join(world_dir, "level.dat"), "rb") as f:
        info = read_level_dat(f.read())
    base = os.path.basename(world_dir)
    parent = os.path.dirname(world_dir)
    dims = [os.path.join(parent, base + s) for s in DIMS[1:]
            if os.path.isdir(os.path.join(parent, base + s))]
    info.update({"world_dir": world_dir, "name": base, "dims": dims,
                 "server_root": server_root, "size": _dir_size(world_dir)
                 + sum(_dir_size(d) for d in dims),
                 "alternatives": alternatives or []})
    return info


def peek_current(full=False):
    """Текущият свят на сървъра. full=True смята и размера (бавно)."""
    path = os.path.join(paths.SERVER, "world")
    lv = os.path.join(path, "level.dat")
    if not os.path.isfile(lv):
        return None
    try:
        with open(lv, "rb") as f:
            info = read_level_dat(f.read())
    except Exception:
        return None
    info.update({"world_dir": path, "name": "world", "dims": [
        os.path.join(paths.SERVER, "world" + s)
        for s in DIMS[1:] if os.path.isdir(os.path.join(paths.SERVER,
                                                       "world" + s))],
        "server_root": None, "alternatives": []})
    info["size"] = (_dir_size(path) + sum(_dir_size(d) for d in info["dims"])
                    ) if full else 0
    return info


# ---------- проверка преди внасяне ----------

def assess(info: dict, server_version: str | None) -> dict:
    """Предупреждения и дали внасянето има смисъл."""
    warns, block = [], None
    wv, sv = ver_tuple(info.get("version")), ver_tuple(server_version)

    if not info.get("version"):
        warns.append("Не мога да разбера от коя версия е светът. Ако "
                     "сървърът откаже да го зареди, свали по-нова версия.")
    elif wv and sv and wv > sv:
        block = (f"Светът е от Minecraft {info['version']}, а сървърът е "
                 f"{server_version}. Minecraft не зарежда свят от по-нова "
                 f"версия. Свали Paper {info['version']} или по-нов.")
    elif wv and sv and wv < sv:
        warns.append(f"Светът е от {info['version']} и ще се надгради до "
                     f"{server_version}. Оригиналът остава непокътнат, но "
                     f"надграденият свят не може да се върне назад.")

    pdir = os.path.join(info["world_dir"], "playerdata")
    if os.path.isdir(pdir):
        names = [f[:-4] for f in os.listdir(pdir) if f.endswith(".dat")]
        online_style = [n for n in names if len(n) == 36 and n[14] == "4"]
        if online_style:
            warns.append(
                f"{len(online_style)} от записите на играчи са с UUID на "
                f"купени акаунти. Ако новият сървър е за TLauncher, "
                f"инвентарите им няма да се намерят и ще започнат наново.")
    if info.get("hardcore"):
        warns.append("Светът е хардкор — един живот.")
    return {"warnings": warns, "blocked": block}


# ---------- вадене от zip ----------

def _safe_extract(zf: zipfile.ZipFile, dest: str, progress=None):
    dest_real = os.path.realpath(dest)
    total = sum(i.file_size for i in zf.infolist()) or 1
    done = 0
    for item in zf.infolist():
        target = os.path.realpath(os.path.join(dest, item.filename))
        if not (target == dest_real or target.startswith(dest_real + os.sep)):
            raise ValueError(f"Опасен път в архива: {item.filename}")
        if item.is_dir():
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(item) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out, 1 << 20)
        done += item.file_size
        if progress:
            progress(done / total * 0.5)


def inspect_zip(path: str) -> dict | None:
    """Чете level.dat направо от архива, без да го разархивира."""
    with zipfile.ZipFile(path) as zf:
        levels = [n for n in zf.namelist()
                  if (n == "level.dat" or n.endswith("/level.dat"))
                  and "__MACOSX" not in n and n.count("/") <= 3]
        if not levels:
            return None
        # Основният свят преди world_nether/world_the_end, после по-плиткия
        dim = lambda n: os.path.dirname(n).endswith(("_nether", "_the_end"))
        levels.sort(key=lambda n: (dim(n), n.count("/")))
        name = levels[0]
        info = read_level_dat(zf.read(name))
        info.update({"world_dir": None, "name": os.path.basename(
            os.path.dirname(name)) or "world", "dims": [], "server_root": None,
            "size": sum(i.file_size for i in zf.infolist()),
            "alternatives": [], "zip": True})
        return info


# ---------- внасянето ----------

def _copy_tree(src, dst, progress, base, span):
    files = []
    for root, dirs, names in os.walk(src):
        for n in names:
            if n not in SKIP:
                files.append(os.path.join(root, n))
    total = sum(os.path.getsize(f) for f in files) or 1
    done = 0
    for f in files:
        rel = os.path.relpath(f, src)
        out = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copyfile(f, out)
        done += os.path.getsize(f)
        if progress:
            progress(base + span * done / total)


def import_world(source: str, merge_whitelist=None, progress=None) -> dict:
    """Внася света. Сървърът ТРЯБВА да е спрян.

    Връща {"ok", "message", "whitelist": [...], "backup": път|None}.
    """
    tmp = None
    try:
        os.makedirs(paths.SERVER, exist_ok=True)
        srv_real = os.path.realpath(paths.SERVER)
        if os.path.realpath(source).startswith(srv_real):
            return {"ok": False, "message": "Това е папката на текущия "
                                            "сървър, не стар свят."}

        if source.lower().endswith(".zip"):
            tmp = os.path.join(paths.SERVER, "_import_tmp")
            shutil.rmtree(tmp, ignore_errors=True)
            os.makedirs(tmp)
            with zipfile.ZipFile(source) as zf:
                _safe_extract(zf, tmp, progress)
            info = find_world(tmp)
            base = 0.5
        else:
            info = find_world(source)
            base = 0.0
        if not info:
            return {"ok": False, "message": "Не намирам свят (липсва "
                                            "level.dat) в избраното."}

        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = None
        moved = []
        for suffix in DIMS:
            cur = os.path.join(paths.SERVER, "world" + suffix)
            if os.path.isdir(cur):
                if not backup:
                    backup = os.path.join(paths.SERVER, "_backups",
                                          f"world-{stamp}")
                    n = 2
                    while os.path.exists(backup):      # две внасяния в една секунда
                        backup = os.path.join(paths.SERVER, "_backups",
                                              f"world-{stamp}-{n}")
                        n += 1
                os.makedirs(backup, exist_ok=True)
                shutil.move(cur, os.path.join(backup, "world" + suffix))
                moved.append(suffix)

        span = 1.0 - base
        try:
            _copy_tree(info["world_dir"], os.path.join(paths.SERVER, "world"),
                       progress, base, span * 0.9)
            for d in info["dims"]:
                suffix = os.path.basename(d)[len(info["name"]):]
                _copy_tree(d, os.path.join(paths.SERVER, "world" + suffix),
                           progress, base + span * 0.9, span * 0.1)
        except Exception:
            # Не оставяме сървъра без свят: махаме наполовина копираното и
            # връщаме стария на мястото му.
            for suffix in DIMS:
                shutil.rmtree(os.path.join(paths.SERVER, "world" + suffix),
                              ignore_errors=True)
            for suffix in moved:
                shutil.move(os.path.join(backup, "world" + suffix),
                            os.path.join(paths.SERVER, "world" + suffix))
            if backup and not os.listdir(backup):
                os.rmdir(backup)
            raise

        names = []
        if merge_whitelist and info.get("server_root"):
            wl = os.path.join(info["server_root"], "whitelist.json")
            try:
                import json
                with open(wl, encoding="utf-8") as f:
                    names = [e["name"] for e in json.load(f) if e.get("name")]
            except (OSError, ValueError, KeyError):
                pass
        if progress:
            progress(1.0)
        log.ok("Свят", f"Внесен „{info.get('level_name') or info['name']}“ "
                       f"({info.get('version') or '?'}).")
        return {"ok": True, "whitelist": names, "backup": backup,
                "message": "Светът е внесен."}
    except Exception as e:
        log.error("Свят", f"Внасянето се провали: {type(e).__name__}: {e}")
        return {"ok": False, "message": f"Не се получи: {e}"}
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
