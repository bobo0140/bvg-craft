"""
manager.py — сваля, настройва и пуска Minecraft сървъра.

Paper се сваля от fill.papermc.io/v3 (старото v2 е изключено от юли
2026). Java се избира според версията: 26.x иска Java 25, 1.20.5-1.21.x
върви на Java 21.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import uuid
import zipfile

import requests

from .. import paths
from ..logbus import log
from . import events

PAPER_API = "https://fill.papermc.io/v3/projects/paper"
PAPER_HEADERS = {"User-Agent":
                 "bvg-craft/1.0 (https://bvgworld.online)"}

COMMON_JAVA = [
    r"C:\Program Files\Eclipse Adoptium",
    r"C:\Program Files\Java",
    r"C:\Program Files\Microsoft\jdk",
    r"C:\Program Files\Zulu",
    r"C:\Program Files\Amazon Corretto",
]

# Флаговете, които Paper препоръчва официално (Aikar, G1)
G1_FLAGS = [
    "-XX:+UseG1GC", "-XX:+ParallelRefProcEnabled",
    "-XX:MaxGCPauseMillis=200", "-XX:+UnlockExperimentalVMOptions",
    "-XX:+DisableExplicitGC", "-XX:+AlwaysPreTouch",
    "-XX:G1NewSizePercent=30", "-XX:G1MaxNewSizePercent=40",
    "-XX:G1HeapRegionSize=8M", "-XX:G1ReservePercent=20",
    "-XX:G1HeapWastePercent=5", "-XX:G1MixedGCCountTarget=4",
    "-XX:InitiatingHeapOccupancyPercent=15",
    "-XX:G1MixedGCLiveThresholdPercent=90",
    "-XX:G1RSetUpdatingPauseTimePercent=5", "-XX:SurvivorRatio=32",
    "-XX:+PerfDisableSharedMem", "-XX:MaxTenuringThreshold=1",
    "-Dusing.aikars.flags=https://mcflags.emc.gs",
    "-Daikars.new.flags=true",
]
# ZGC — по-малки паузи; на Java 25 е генерационен по подразбиране
ZGC_FLAGS = ["-XX:+UseZGC", "-XX:+UseStringDeduplication",
             "-XX:+AlwaysPreTouch", "-XX:TrimNativeHeapInterval=5000"]


# ---------- версии и Java ----------

def required_java(version: str | None) -> int:
    if not version:
        return 25
    head = version.split(".")[0]
    if head.isdigit() and int(head) >= 26:
        return 25
    try:
        p = [int(x) for x in version.split(".")[:3]]
    except ValueError:
        return 25
    if p[:2] >= [1, 21] or p[:3] >= [1, 20, 5]:
        return 21
    return 17


def find_java() -> str | None:
    for root in (paths.JDK,):
        if os.path.isdir(root):
            for entry in os.listdir(root):
                c = os.path.join(root, entry, "bin", "java.exe")
                if os.path.exists(c):
                    return c
            c = os.path.join(root, "bin", "java.exe")
            if os.path.exists(c):
                return c
    home = os.environ.get("JAVA_HOME")
    if home and os.path.exists(os.path.join(home, "bin", "java.exe")):
        return os.path.join(home, "bin", "java.exe")
    found = shutil.which("java")
    if found:
        return found
    for base in COMMON_JAVA:
        if os.path.isdir(base):
            for entry in sorted(os.listdir(base), reverse=True):
                c = os.path.join(base, entry, "bin", "java.exe")
                if os.path.exists(c):
                    return c
    return None


def java_version(java: str | None) -> int | None:
    if not java:
        return None
    try:
        out = subprocess.run(
            [java, "-version"], capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        m = re.search(r'version "?(\d+)', (out.stderr or "") + (out.stdout or ""))
        return int(m.group(1)) if m else None
    except Exception:
        return None


def download_java(major: int, progress=None) -> str | None:
    url = (f"https://api.adoptium.net/v3/binary/latest/{major}/ga/"
           "windows/x64/jdk/hotspot/normal/eclipse")
    if os.path.isdir(paths.JDK):
        shutil.rmtree(paths.JDK, ignore_errors=True)
    os.makedirs(paths.JDK, exist_ok=True)
    archive = os.path.join(paths.JDK, "jdk.zip")
    try:
        log.info("Java", f"Свалям Java {major} (~200 MB)...")
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            done = 0
            with open(archive, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress(done / total)
        log.info("Java", "Разархивирам...")
        with zipfile.ZipFile(archive) as z:
            z.extractall(paths.JDK)
        os.remove(archive)
        java = find_java()
        log.ok("Java", f"Готово: Java {major}")
        return java
    except Exception as e:
        log.error("Java", f"Свалянето се провали: {e}")
        return None


def list_versions() -> list[str]:
    """Стабилните версии на Paper, най-новата първа."""
    try:
        r = requests.get(PAPER_API, headers=PAPER_HEADERS, timeout=30)
        r.raise_for_status()
        groups = r.json().get("versions", {})
    except Exception as e:
        log.warn("Paper", f"Не мога да взема версиите: {e}")
        return []
    out = []
    for names in groups.values():
        for n in names:
            low = n.lower()
            if not any(x in low for x in ("-rc", "-pre", "snapshot")):
                out.append(n)
    return out


def find_paper() -> str | None:
    if not os.path.isdir(paths.SERVER):
        return None
    jars = sorted(f for f in os.listdir(paths.SERVER)
                  if f.startswith("paper-") and f.endswith(".jar"))
    return os.path.join(paths.SERVER, jars[-1]) if jars else None


def paper_version(jar=None) -> str | None:
    jar = jar or find_paper()
    if not jar:
        return None
    m = re.search(r"paper-(\d+\.\d+(?:\.\d+)?)", os.path.basename(jar))
    return m.group(1) if m else None


def download_paper(version=None) -> str | None:
    os.makedirs(paths.SERVER, exist_ok=True)
    try:
        versions = list_versions()
        if not version:
            if not versions:
                raise RuntimeError("няма списък с версии")
            version = versions[0]
        log.info("Paper", f"Търся build за {version}...")
        r = requests.get(f"{PAPER_API}/versions/{version}/builds",
                         headers=PAPER_HEADERS, timeout=30)
        r.raise_for_status()
        builds = r.json()
        chosen = next((b for b in builds
                       if str(b.get("channel", "")).upper() == "STABLE"),
                      builds[0] if builds else None)
        if not chosen:
            raise RuntimeError("няма build")
        dl = (chosen.get("downloads") or {}).get("server:default") or {}
        url, name = dl.get("url"), dl.get("name")
        if not url:
            raise RuntimeError("няма файл за сваляне")
        name = name or f"paper-{version}-{chosen.get('id')}.jar"
        dest = os.path.join(paths.SERVER, name)
        log.info("Paper", f"Свалям {name}...")
        with requests.get(url, headers=PAPER_HEADERS, stream=True,
                          timeout=180) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(1 << 20):
                    f.write(chunk)
        for f in os.listdir(paths.SERVER):
            if f.startswith("paper-") and f.endswith(".jar") and f != name:
                try:
                    os.remove(os.path.join(paths.SERVER, f))
                except OSError:
                    pass
        log.ok("Paper", f"Готово: {name}")
        return dest
    except Exception as e:
        log.error("Paper", f"Свалянето се провали: {e}")
        return None


# ---------- whitelist ----------

def offline_uuid(name: str) -> str:
    """Идентификаторът, който Minecraft сам смята за офлайн играч.

    Командата `whitelist add` пита Mojang за профил и се проваля за
    имена без купен акаунт. Затова пишем файла сами.
    """
    d = bytearray(hashlib.md5(b"OfflinePlayer:" + name.encode()).digest())
    d[6] = (d[6] & 0x0F) | 0x30
    d[8] = (d[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(d)))


def write_whitelist(names, offline=True):
    names = [n.strip() for n in dict.fromkeys(names) if n and n.strip()]
    entries = [{"uuid": offline_uuid(n) if offline else "", "name": n}
               for n in names]
    os.makedirs(paths.SERVER, exist_ok=True)
    with open(os.path.join(paths.SERVER, "whitelist.json"), "w",
              encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    return len(entries)


def write_ops(names, offline=True):
    entries = [{"uuid": offline_uuid(n) if offline else "", "name": n,
                "level": 4, "bypassesPlayerLimit": True}
               for n in dict.fromkeys(names) if n]
    with open(os.path.join(paths.SERVER, "ops.json"), "w",
              encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


# ---------- server.properties ----------

def write_properties(cfg):
    os.makedirs(paths.SERVER, exist_ok=True)
    with open(os.path.join(paths.SERVER, "eula.txt"), "w") as f:
        f.write("eula=true\n")

    want = {
        "enable-rcon": "true",
        "rcon.port": str(cfg.get("rcon_port")),
        "rcon.password": cfg.get("rcon_password"),
        "broadcast-rcon-to-ops": "false",
        "online-mode": "false" if cfg.get("offline_mode") else "true",
        "enforce-secure-profile": "false",
        "white-list": "true" if cfg.get("whitelist_on") else "false",
        "enforce-whitelist": "true" if cfg.get("whitelist_on") else "false",
        "server-ip": "" if cfg.get("open_to_network") else "127.0.0.1",
        "server-port": str(cfg.get("port")),
        "difficulty": cfg.get("difficulty"),
        "gamemode": cfg.get("gamemode"),
        "max-players": str(cfg.get("max_players")),
        "view-distance": str(cfg.get("view_distance")),
        "simulation-distance": str(cfg.get("simulation_distance")),
        "motd": cfg.get("motd"),
        "pvp": "true" if cfg.get("pvp") else "false",
        "hardcore": "true" if cfg.get("hardcore") else "false",
        "spawn-protection": str(cfg.get("spawn_protection")),
        "enable-command-block": "true",
        "allow-flight": "true",
        "sync-chunk-writes": "false",   # по-малко засичания на диска
    }
    path = os.path.join(paths.SERVER, "server.properties")
    existing = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    existing[k.strip()] = v.strip()
    existing.update(want)
    with open(path, "w", encoding="utf-8") as f:
        for k, v in sorted(existing.items()):
            f.write(f"{k}={v}\n")


# ---------- процесът ----------

class Server:
    def __init__(self, cfg, on_event=None):
        self.cfg = cfg
        self.on_event = on_event
        self.proc = None
        self.ready = False
        self.online = set()

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def build_command(self, java, jar):
        ram = max(1, int(self.cfg.get("ram_gb")))
        cmd = [java, f"-Xms{ram}G", f"-Xmx{ram}G"]
        if self.cfg.get("gc_profile") == "zgc":
            cmd += ZGC_FLAGS
            if (java_version(java) or 0) >= 25:
                cmd.append("-XX:+UseCompactObjectHeaders")
        else:
            cmd += G1_FLAGS
        cmd += ["-jar", jar, "--nogui"]
        return cmd

    def start(self):
        if self.running:
            return True, "Вече върви."
        jar = find_paper()
        if not jar:
            return False, "Няма свален сървър. Натисни „Свали Paper“."
        java = find_java()
        need = required_java(paper_version(jar))
        have = java_version(java)
        if not java or (have is not None and have < need):
            return False, (f"Сървърът иска Java {need}"
                           + (f", а имаш {have}." if have else "."))

        write_properties(self.cfg)
        if self.cfg.get("whitelist_on"):
            write_whitelist(self.cfg.get("whitelist") or [],
                            offline=self.cfg.get("offline_mode"))
        if self.cfg.get("admins"):
            write_ops(self.cfg.get("admins"),
                      offline=self.cfg.get("offline_mode"))

        cmd = self.build_command(java, jar)
        log.info("Сървър", f"Пускам {os.path.basename(jar)} с "
                           f"{self.cfg.get('ram_gb')} GB "
                           f"({self.cfg.get('gc_profile').upper()})")
        self.ready = False
        self.online.clear()
        self.proc = subprocess.Popen(
            cmd, cwd=paths.SERVER, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._pump, daemon=True).start()
        return True, "Пуска се..."

    def _pump(self):
        for line in self.proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            log.console(line)
            ev = events.parse(line, self.online)
            if not ev:
                continue
            if ev["type"] == "ready":
                self.ready = True
            elif ev["type"] == "join":
                self.online.add(ev["player"])
            elif ev["type"] == "leave":
                self.online.discard(ev["player"])
            if self.on_event:
                try:
                    self.on_event(ev)
                except Exception as e:
                    log.error("Събития", f"{type(e).__name__}: {e}")
        self.ready = False
        self.online.clear()
        log.warn("Сървър", "Спрян.")
        if self.on_event:
            self.on_event({"type": "stopped"})

    def command(self, text):
        if self.running:
            try:
                self.proc.stdin.write(text + "\n")
                self.proc.stdin.flush()
            except OSError:
                pass

    def stop(self, wait=60):
        if not self.running:
            return
        log.info("Сървър", "Спирам и записвам света...")
        self.command("stop")
        try:
            self.proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            log.warn("Сървър", "Спрян насила.")
