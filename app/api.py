"""
api.py — това, което интерфейсът може да вика.

Всеки метод връща речник. Дългите неща (сваляния, пускане на сървъра)
вървят във фонова нишка и докладват през лога, за да не замръзва
прозорецът.
"""

import json
import os
import subprocess
import sys
import threading
import time

from . import paths
from .ai import brain as brain_mod, characters, providers
from .config import Config
from .game import events_lib
from .logbus import log
from .server import manager
from .server.rcon import Sender


def _bg(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


class Api:
    def __init__(self):
        self._cfg = Config()
        self._sender = Sender(self._cfg)
        self._server = manager.Server(self._cfg, on_event=self._on_event)
        self._brain = brain_mod.Brain(self._cfg, self._sender, self._server)
        self._window = None
        self._progress = None
        self._versions = []
        self._java_cache = None
        log.ok("BVG Craft", "Готов.")

    # ---------- свързване ----------

    def _on_event(self, ev):
        if ev["type"] == "ready":
            self._sender.enabled = True
            self._brain.start()
            log.ok("Сървър", "Готов за игра.")
        elif ev["type"] == "stopped":
            self._sender.enabled = False
            self._brain.stop()
        elif ev["type"] == "join":
            log.info("Играчи", f"{ev['player']} влезе")
        elif ev["type"] == "leave":
            log.info("Играчи", f"{ev['player']} излезе")
        elif ev["type"] == "death":
            log.info("Смърт", ev["message"])
        self._brain.on_event(ev)

    # ---------- състояние ----------

    def state(self):
        java = self._java_cache
        if java is None:
            path = manager.find_java()
            java = {"path": path, "version": manager.java_version(path)}
            self._java_cache = java
        jar = manager.find_paper()
        ver = manager.paper_version(jar)
        need = manager.required_java(ver)
        return {
            "running": self._server.running,
            "ready": self._server.ready,
            "online": sorted(self._server.online),
            "paper": os.path.basename(jar) if jar else None,
            "mc_version": ver,
            "java": java["version"],
            "java_needed": need,
            "java_ok": bool(java["version"] and java["version"] >= need),
            "event": self._brain.engine.status(),
            "progress": self._progress,
            "config": self._cfg.public(),
            "characters": {k: {"name": v["name"], "color": v["color"],
                               "aliases": v["aliases"][:3],
                               "placed": bool((self._cfg.get("characters")
                                               or {}).get(k, {}).get("pos")),
                               "enabled": (self._cfg.get("characters") or {})
                               .get(k, {}).get("enabled", False)}
                           for k, v in characters.CHARACTERS.items()},
            "events": [{"key": k, "title": c.title,
                        "min_players": c.min_players}
                       for k, c in events_lib.LIBRARY.items()],
            "ai_busy": self._brain.busy_count,
            "ip": self._lan_ip(),
        }

    def logs(self, since=0):
        return log.since(int(since or 0))

    # ---------- настройки ----------

    def save(self, values):
        try:
            self._cfg.update(values or {})
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def versions(self):
        if not self._versions:
            self._versions = manager.list_versions()
        return self._versions

    # ---------- сваляния ----------

    def get_java(self):
        ver = manager.paper_version()
        need = manager.required_java(ver)

        def work():
            self._progress = {"what": f"Java {need}", "value": 0}
            manager.download_java(
                need, progress=lambda f: self._progress.update(value=f))
            self._progress = None
            self._java_cache = None
        _bg(work)
        return {"ok": True}

    def get_paper(self, version=None):
        def work():
            self._progress = {"what": "Paper", "value": 0.5}
            manager.download_paper(version or self._cfg.get("mc_version")
                                   or None)
            self._progress = None
            self._java_cache = None
        _bg(work)
        return {"ok": True}

    # ---------- сървър ----------

    def start(self):
        ok, msg = self._server.start()
        (log.ok if ok else log.error)("Сървър", msg)
        return {"ok": ok, "message": msg}

    def stop(self):
        _bg(self._server.stop)
        return {"ok": True}

    def console(self, text):
        text = (text or "").strip().lstrip("/")
        if not text:
            return {"ok": False}
        if self._server.running:
            self._server.command(text)
            log.info("Конзола", f"> {text}")
            return {"ok": True}
        return {"ok": False, "error": "Сървърът не върви."}

    # ---------- играчи ----------

    def whitelist_add(self, name):
        name = (name or "").strip()
        if not name or len(name) > 16:
            return {"ok": False, "error": "Името трябва да е до 16 знака."}
        wl = list(self._cfg.get("whitelist") or [])
        if name not in wl:
            wl.append(name)
            self._cfg.set("whitelist", wl)
        self._apply_whitelist()
        log.ok("Whitelist", f"{name} е добавен.")
        return {"ok": True}

    def whitelist_remove(self, name):
        wl = [n for n in (self._cfg.get("whitelist") or []) if n != name]
        self._cfg.set("whitelist", wl)
        self._apply_whitelist()
        if self._server.running and name in self._server.online \
                and self._cfg.get("whitelist_on"):
            self._server.command(f"kick {name} Премахнат от списъка")
        log.info("Whitelist", f"{name} е махнат.")
        return {"ok": True}

    def toggle_admin(self, name):
        admins = list(self._cfg.get("admins") or [])
        if name in admins:
            admins.remove(name)
        else:
            admins.append(name)
        self._cfg.set("admins", admins)
        manager.write_ops(admins, offline=self._cfg.get("offline_mode"))
        # Файлът важи при следващо пускане; за вече влизал играч
        # командата от конзолата действа веднага.
        if self._server.running:
            self._server.command(("op " if name in admins else "deop ") + name)
        log.info("Админи", f"{name} {'е' if name in admins else 'не е'} "
                           f"администратор.")
        return {"ok": True, "admin": name in admins}

    def _apply_whitelist(self):
        manager.write_whitelist(self._cfg.get("whitelist") or [],
                                offline=self._cfg.get("offline_mode"))
        if self._server.running:
            self._server.command("whitelist reload")

    # ---------- герои и събития ----------

    def place(self, key, player):
        if not self._server.ready:
            return {"ok": False, "error": "Сървърът не е готов."}
        if not player:
            return {"ok": False, "error": "Избери играч, до когото да е."}
        ok, msg = self._brain.place_npc(key, player)
        (log.ok if ok else log.error)("Герои", msg)
        return {"ok": ok, "message": msg}

    def remove(self, key):
        self._brain.remove_npc(key)
        return {"ok": True}

    def event(self, key):
        if not self._server.ready:
            return {"ok": False, "error": "Сървърът не е готов."}
        ok, msg = self._brain.start_event(key, source="ръчно")
        return {"ok": ok, "message": msg}

    def stop_event(self):
        self._brain.engine.stop()
        return {"ok": True}

    def prank(self, player):
        if player in self._server.online:
            self._brain.prank(player)
            return {"ok": True}
        return {"ok": False, "error": "Няма такъв играч онлайн."}

    def say_as(self, key, text, player=None):
        """Говориш на герой от приложението — като собственик."""
        admin = (self._cfg.get("admins") or [None])[0] or "BVG"
        who = player or admin
        _bg(self._brain.talk, key, who, text)
        return {"ok": True}

    def test_ai(self):
        reply, err = providers.ask(
            self._cfg, characters.system_prompt("keeper"),
            [{"role": "user", "content": "Кажи едно кратко изречение за "
                                         "проверка. Без команди."}],
            force=True)
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "say": reply.get("say", "")}

    # ---------- разни ----------

    def open_folder(self, which="app"):
        path = {"app": paths.app_dir(), "server": paths.SERVER,
                "data": paths.DATA}.get(which, paths.app_dir())
        os.makedirs(path, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass
        return {"ok": True}

    @staticmethod
    def _lan_ip():
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _shutdown(self):
        self._brain.stop()
        if self._server.running:
            self._server.stop(wait=45)
