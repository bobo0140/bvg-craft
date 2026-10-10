"""
api.py — това, което интерфейсът може да вика.

Само методите без долна черта се виждат от интерфейса. Всичко вътрешно
започва с _, иначе прозорецът би изложил и сървъра, и сокетите.
Дългите неща вървят във фонова нишка, за да не замръзва прозорецът.
"""

import ipaddress
import json
import os
import re
import subprocess
import sys
import threading
import time

from . import paths
from .ai import brain as brain_mod, characters, providers
from .config import Config
from .game import blueprints, events_lib, guide, world
from .logbus import log
from .server import manager, worldio
from .server.monitor import Monitor
from .server.rcon import Sender, classify_reply
from .version import BUILT, VERSION


def _bg(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


def classify_ip(ip):
    """Какъв е адресът — помага да се разбере откъде влиза играчът."""
    try:
        a = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return "неизвестен"
    if a.is_loopback:
        return "този компютър или playit.gg"
    if a.version == 4:
        if str(a).startswith("26."):
            return "Radmin VPN"
        if a in ipaddress.ip_network("100.64.0.0/10"):
            return "VPN или CGNAT"
    if a.is_private or a.is_link_local:
        return "локална мрежа"
    return "интернет"


class _Capture:
    """Събира команди, вместо да ги праща — за пробата."""

    def __init__(self):
        self.cmds = []

    def send(self, command, optional=False):
        self.cmds.append(command)

    def send_many(self, commands, optional=False):
        self.cmds.extend(commands)




class _OnlyProvider:
    """Настройки, в които има ключ само за една услуга — за проверката."""

    def __init__(self, cfg, prov):
        self.cfg, self.prov = cfg, prov

    def get(self, key, default=None):
        if key == "ai_provider":
            return self.prov
        if key.endswith("_key") and key != providers.PROVIDERS[self.prov]["key"]:
            return ""
        return self.cfg.get(key, default)


class Api:
    def __init__(self):
        self._cfg = Config()
        self._sender = Sender(self._cfg)
        self._server = manager.Server(self._cfg, on_event=self._on_event)
        # Дългите команди (книгата, големи надписи) не минават през RCON
        self._sender.console = self._server.command
        self._brain = brain_mod.Brain(self._cfg, self._sender, self._server)
        self._monitor = Monitor(
            self._server, self._sender,
            on_ready=lambda: self._on_event({"type": "ready"}),
            on_event=self._on_event)
        self._window = None
        self._progress = None
        self._java_cache = None
        self._ip_cache = None
        self._ready_done = False
        self._import_result = None
        self._importing = False
        self._last_state = None
        self._state_err_at = 0.0
        self._monitor.start()
        log.ok("BVG Craft", "Готов.")

    # ---------- свързване ----------

    def _on_event(self, ev):
        t = ev["type"]
        if t == "ready" and not self._ready_done:
            self._ready_done = True
            self._sender.enabled = True
            self._brain.start()
            log.ok("Сървър", "Готов за игра.")
        elif t == "ready":
            return
        elif t == "stopped":
            self._ready_done = False
            self._sender.enabled = False
            self._brain.stop()
        elif t == "join" and not ev.get("synthetic"):
            log.info("Играчи", f"{ev['player']} влезе")
        elif t == "leave" and not ev.get("synthetic"):
            log.info("Играчи", f"{ev['player']} излезе")
        elif t == "login":
            log.info("Играчи", f"{ev['player']} се свърза от {ev['ip']} "
                               f"({classify_ip(ev['ip'])})")
        elif t == "death":
            log.info("Смърт", ev["message"])
        self._brain.on_event(ev)

    # ---------- състояние ----------

    def state(self):
        try:
            self._last_state = self._build_state()
            return self._last_state
        except Exception as e:
            if time.time() - self._state_err_at > 30:
                self._state_err_at = time.time()
                log.error("Табло", f"Не успях да събера състоянието: "
                                   f"{type(e).__name__}: {e}")
            if self._last_state:
                return dict(self._last_state, state_error=str(e))
            raise

    def _build_state(self):
        if self._java_cache is None:
            path = manager.find_java()
            self._java_cache = {"path": path,
                                "version": manager.java_version(path)}
        java = self._java_cache
        jar = manager.find_paper()
        ver = manager.paper_version(jar)
        need = manager.required_java(ver)
        srv = self._server
        online = sorted(srv.online)
        placed = self._cfg.get("characters") or {}
        return {
            "running": srv.running, "ready": srv.ready, "online": online,
            "paper": os.path.basename(jar) if jar else None,
            "mc_version": ver, "java": java["version"],
            "java_needed": need,
            "java_ok": bool(java["version"] and java["version"] >= need),
            "event": self._brain.engine.status(),
            "progress": self._progress, "ip": self._lan_ip(),
            "config": self._cfg.public(),
            "roster": self._roster(),
            "health": self._health(),
            "ai": self._ai_status(),
            "import_result": self._import_result,
            "characters": {
                k: {"name": v["name"], "color": v["color"],
                    "aliases": v["aliases"][:3],
                    "placed": bool(placed.get(k, {}).get("pos")),
                    "enabled": placed.get(k, {}).get("enabled", False)}
                for k, v in characters.CHARACTERS.items()},
            "events": [{"key": k, "title": c.title,
                        "min_players": c.min_players}
                       for k, c in events_lib.LIBRARY.items()],
            "contests": [{"key": k, "title": v["title"], "desc": v["desc"]}
                         for k, v in events_lib.CONTESTS.items()],
            "gm": self._gm_status(),
            "villages": self._brain.villages.status(),
            "story": self._story(),
            "quests": self._brain.quests.status(),
            "fame": self._brain.fame.top(10),
            "visits": self._brain.visits.status(),
            "bosses": [b["name"] for b in events_lib.BOSS_PRESETS],
            "kinds": [{"key": k, "name": n} for k, n in
                      blueprints.KIND_NAMES.items() if k != "plaza"],
            "styles": [{"key": k, "name": n} for k, n in
                       blueprints.STYLE_NAMES.items()],
            "owner": self._cfg.get("owner_name") or "",
            "version": VERSION, "built": BUILT,
            "rejected": len(self._sender.rejected),
        }

    def _roster(self):
        cfg, srv = self._cfg, self._server
        wl = list(cfg.get("whitelist") or [])
        admins = set(cfg.get("admins") or [])
        online = set(srv.online)
        known = self._brain.players
        out = []
        for n in list(dict.fromkeys(wl + sorted(online))):
            ip = srv.ips.get(n) or self._brain.last_ip(n)
            out.append({
                "name": n, "online": n in online, "whitelisted": n in wl,
                "admin": n in admins, "ip": ip,
                "ip_kind": classify_ip(ip) if ip else None,
                "deaths": (known.get(n) or {}).get("deaths", 0),
                "visits": (known.get(n) or {}).get("visits", 0),
                "fame": (known.get(n) or {}).get("fame", 0),
                "rank": self._rank_name((known.get(n) or {}).get("fame", 0))})
        return out

    @staticmethod
    def _rank_name(pts):
        from .game.fame import RANKS, rank_of
        return RANKS[rank_of(int(pts or 0))][1]

    def _story(self):
        d = self._brain.director
        st = d.story
        now = time.time()
        return {"title": st.get("title") or "", "chapter": st.get("chapter")
                or 0, "goal": st.get("goal") or "",
                "chronicle": [{"ago": int(now - t), "text": x}
                              for t, x in st.get("chronicle", [])[-8:]][::-1]}

    def _health(self):
        """Какво работи — за да не се гадае защо нещо мълчи."""
        srv, mon, snd = self._server, self._monitor, self._sender
        now = time.time()
        run = srv.running
        out = []

        if not run:
            out.append({"name": "Конзола", "ok": None,
                        "detail": "Сървърът не върви."})
            out.append({"name": "Връзка с играта", "ok": None,
                        "detail": "Сървърът не върви."})
            out.append({"name": "Играчи", "ok": None, "detail": "—"})
        else:
            ago = int(now - srv.last_line) if srv.last_line else None
            out.append({"name": "Конзола", "ok": ago is not None,
                        "detail": f"последен ред преди {ago} сек"
                        if ago is not None else "още няма редове"})
            if not srv.ready and not mon.rcon_ok:
                out.append({"name": "Връзка с играта", "ok": None,
                            "detail": "изчаквам светът да зареди"})
            else:
                out.append({"name": "Връзка с играта", "ok": bool(mon.rcon_ok),
                            "detail": "RCON отговаря" if mon.rcon_ok
                            else (snd.last_error or "още не отговаря")})
            ll = mon.last_list
            if ll is None:
                out.append({"name": "Играчи", "ok": None,
                            "detail": "още не съм питал сървъра"})
            else:
                listed, known = set(ll[3]), set(srv.online)
                same = listed == known
                out.append({"name": "Играчи", "ok": same,
                            "detail": f"{ll[1]} в света" if same else
                            f"сървърът казва {ll[1]}, водя {len(known)}"})

        st = providers.STATS
        keyed = providers.configured(self._cfg)
        if not keyed:
            out.append({"name": "Изкуствен разум", "ok": False,
                        "detail": "Няма ключ. Сложи поне един безплатен "
                                  "(Gemini или Groq) в раздел AI."})
        elif st["last_error"] and st["last_error_at"] > st["last_ok"]:
            out.append({"name": "Изкуствен разум", "ok": False,
                        "detail": st["last_error"][:120]})
        elif st["ok"]:
            names = ", ".join(providers.PROVIDERS[p]["name"] for p in keyed)
            out.append({"name": "Изкуствен разум", "ok": True,
                        "detail": f"{st['ok']} отговора · {names}"})
        else:
            out.append({"name": "Изкуствен разум", "ok": None,
                        "detail": "Ключът е сложен, още не е питан."})
        return out

    def _ai_status(self):
        st = providers.STATS
        ms = providers.model_status(self._cfg)
        prov = ms.get("provider") or ""
        return {"ok": st["ok"], "err": st["err"], "busy": st["busy"],
                "last_error": st["last_error"],
                "last_ok_ago": int(time.time() - st["last_ok"])
                if st["last_ok"] else None,
                "model": ms["working"], "models": ms["models"],
                "provider": providers.PROVIDERS.get(prov, {}).get("name", ""),
                "providers": ms["providers"], "today": ms["today"],
                "configured": len(providers.configured(self._cfg)),
                "chosen": ms["chosen"], "unavailable": ms["unavailable"],
                "list_error": ms["list_error"]}

    def _gm_status(self):
        d = self._brain.director
        now = time.time()
        return {"enabled": bool(self._cfg.get("gm_enabled", True)),
                "busy": d.busy,
                "next_in": max(0, int(d.next_at - now))
                if self._server.ready else None,
                "source": d.last_source,
                "decisions": [{"ago": int(now - t), "text": text,
                               "source": src, "done": done}
                              for t, text, src, done in
                              list(d.decisions)[-6:]][::-1]}

    def logs(self, since=0):
        return log.since(int(since or 0))

    # ---------- настройки ----------

    def save(self, values):
        try:
            values = values or {}
            keys = [p["key"] for p in providers.PROVIDERS.values()]
            new_key = any(values.get(k) and not str(values[k]).startswith("•")
                          for k in keys)
            self._cfg.update(values)
            if new_key:
                providers.forget_models()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def versions(self):
        return manager.list_versions()

    # ---------- сваляния ----------

    def get_java(self, *_ignored):
        need = manager.required_java(manager.paper_version())

        def work():
            self._progress = {"what": f"Java {need}", "value": 0}
            manager.download_java(
                need, progress=lambda f: self._progress.update(value=f)
                if self._progress else None)
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
        if self._importing:
            return {"ok": False, "error": "Тече внасяне на свят. Изчакай."}
        jar = manager.find_paper()
        cur = worldio.peek_current()
        if cur and jar:
            blocked = worldio.assess(cur, manager.paper_version(jar))["blocked"]
            if blocked:
                log.error("Сървър", blocked)
                return {"ok": False, "error": blocked}
        ok, msg = self._server.start()
        (log.ok if ok else log.error)("Сървър", msg)
        return {"ok": ok, "message": msg if ok else None,
                "error": None if ok else msg}

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
        if not re.fullmatch(r"[A-Za-z0-9_]{1,16}", name):
            return {"ok": False, "error": "Името е до 16 знака: латински "
                                          "букви, цифри и _."}
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
        if self._server.running:
            self._server.command(("op " if name in admins else "deop ")
                                 + name)
        log.info("Админи", f"{name} {'е' if name in admins else 'не е'} "
                           f"администратор.")
        return {"ok": True, "admin": name in admins}

    def _apply_whitelist(self):
        manager.write_whitelist(self._cfg.get("whitelist") or [],
                                offline=self._cfg.get("offline_mode"))
        if self._server.running:
            self._server.command("whitelist reload")

    # ---------- стар свят ----------

    def world_current(self):
        """Какъв е светът в момента."""
        info = worldio.peek_current(full=True)
        if not info:
            return {"ok": True, "world": None}
        return {"ok": True, "world": self._public_world(info)}

    def world_pick(self, kind="folder"):
        """Отваря прозорец за избор. Връща избрания път."""
        if not self._window:
            return {"ok": False, "error": "Прозорецът не е готов."}
        try:
            import webview
            fd = getattr(webview, "FileDialog", None)
            if kind == "zip":
                mode = fd.OPEN if fd else webview.OPEN_DIALOG
                res = self._window.create_file_dialog(
                    mode, allow_multiple=False,
                    file_types=("Архив (*.zip)", "Всички файлове (*.*)"))
            else:
                mode = fd.FOLDER if fd else webview.FOLDER_DIALOG
                res = self._window.create_file_dialog(mode)
        except Exception as e:
            return {"ok": False, "error": f"Не мога да отворя прозорец: {e}"}
        if not res:
            return {"ok": True, "path": None}
        return {"ok": True, "path": res[0] if isinstance(res, (list, tuple))
                else res}

    def world_inspect(self, path):
        path = (path or "").strip().strip('"')
        if not path or not os.path.exists(path):
            return {"ok": False, "error": "Този път не съществува."}
        try:
            if os.path.isfile(path):
                if not path.lower().endswith(".zip"):
                    return {"ok": False, "error": "Файлът трябва да е .zip."}
                info = worldio.inspect_zip(path)
            else:
                info = worldio.find_world(path)
        except Exception as e:
            return {"ok": False, "error": f"Не мога да го прочета: {e}"}
        if not info:
            return {"ok": False, "error": "Не намирам свят там — "
                                          "липсва файл level.dat."}
        verdict = worldio.assess(info, manager.paper_version())
        return {"ok": True, "world": self._public_world(info),
                "warnings": verdict["warnings"],
                "blocked": verdict["blocked"], "path": path,
                "whitelist_names": self._old_whitelist(info)}

    def world_import(self, path, merge_whitelist=True):
        if self._server.running:
            return {"ok": False, "error": "Първо спри сървъра. Светът не "
                                          "може да се сменя, докато върви."}
        if self._importing:
            return {"ok": False, "error": "Вече тече внасяне."}
        check = self.world_inspect(path)
        if not check["ok"]:
            return check
        if check["blocked"]:
            return {"ok": False, "error": check["blocked"]}

        def work():
            self._importing = True
            self._import_result = None
            self._progress = {"what": "света", "verb": "Внасям", "value": 0}
            try:
                res = worldio.import_world(
                    path, merge_whitelist=bool(merge_whitelist),
                    progress=lambda f: self._progress.update(value=f)
                    if self._progress else None)
                added = []
                if res.get("ok") and res.get("whitelist"):
                    wl = list(self._cfg.get("whitelist") or [])
                    added = [n for n in res["whitelist"] if n not in wl]
                    if added:
                        self._cfg.set("whitelist", wl + added)
                        manager.write_whitelist(
                            wl + added, offline=self._cfg.get("offline_mode"))
                if res.get("ok"):
                    self._brain.villages.reset()
                if res.get("ok") and self._cfg.get("characters"):
                    # Местата на духовете са от стария свят — там може да
                    # има планина или море. Призовават се наново.
                    self._cfg.set("characters", {})
                    log.info("Духове", "Новият свят е внесен — призови "
                                       "духовете наново, където искаш.")
                self._import_result = {
                    "id": time.time(), "ok": bool(res.get("ok")),
                    "message": res.get("message"),
                    "backup": res.get("backup"), "added": added}
            finally:
                self._progress = None
                self._importing = False
        _bg(work)
        return {"ok": True, "message": "Внасям света..."}

    @staticmethod
    def _old_whitelist(info):
        root = info.get("server_root")
        if not root:
            return []
        try:
            import json
            with open(os.path.join(root, "whitelist.json"),
                      encoding="utf-8") as f:
                return [e["name"] for e in json.load(f) if e.get("name")]
        except (OSError, ValueError, KeyError):
            return []

    @staticmethod
    def _public_world(info):
        lp = info.get("last_played") or 0
        return {
            "name": info.get("level_name") or info.get("name"),
            "folder": info.get("name"),
            "version": info.get("version") or "",
            "last_played": time.strftime("%d.%m.%Y %H:%M",
                                         time.localtime(lp)) if lp else "",
            "size_mb": round((info.get("size") or 0) / 1048576),
            "hardcore": bool(info.get("hardcore")),
            "nether": bool(info.get("dims")),
            "zip": bool(info.get("zip")),
            "alternatives": [os.path.basename(a)
                             for a in info.get("alternatives", [])],
        }

    # ---------- герои и събития ----------

    def place(self, key, player):
        if not self._server.ready:
            return {"ok": False, "error": "Сървърът не е готов."}
        if not player:
            return {"ok": False, "error": "Избери играч, до когото да е."}
        ok, msg = self._brain.place_npc(key, player)
        (log.ok if ok else log.error)("Герои", msg)
        return {"ok": ok, "message": msg if ok else None,
                "error": None if ok else msg}

    def remove(self, key):
        self._brain.remove_npc(key)
        return {"ok": True}

    def event(self, key):
        if not self._server.ready:
            return {"ok": False, "error": "Сървърът не е готов."}
        ok, msg = self._brain.start_event(key, source="ръчно")
        return {"ok": ok, "message": msg if ok else None,
                "error": None if ok else msg}

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
        online = sorted(self._server.online)
        admins = [a for a in (self._cfg.get("admins") or []) if a in online]
        who = player or (admins[0] if admins else
                         (online[0] if online else
                          ((self._cfg.get("admins") or ["BVG"])[0])))
        _bg(self._brain.talk, key, who, text)
        return {"ok": True}

    def clear_key(self, prov):
        info = providers.PROVIDERS.get(prov)
        if not info:
            return {"ok": False, "error": "Няма такава услуга."}
        self._cfg.set(info["key"], "")
        providers.forget_models()
        return {"ok": True, "message": f"Ключът за {info['name']} е махнат."}

    def test_ai(self, only=None):
        """Пробва всяка услуга с ключ поотделно. -> кой отговаря и кой не."""
        keyed = providers.configured(self._cfg)
        if only:
            keyed = [p for p in keyed if p == only]
        if not keyed:
            return {"ok": False, "error": "Няма ключ.", "results": []}
        results = []
        for prov in keyed:
            info = providers.PROVIDERS[prov]
            key = self._cfg.get(info["key"])
            models, lerr = providers.list_models(prov, key, refresh=True)
            cfg = _OnlyProvider(self._cfg, prov)
            t0 = time.time()
            reply, err = providers.ask(
                cfg, characters.system_prompt("keeper"),
                [{"role": "user", "content": "Кажи едно кратко изречение за "
                                             "проверка. Без команди."}],
                force=True, max_tokens=600, timeout=40, role="fast")
            results.append({"provider": prov, "name": info["name"],
                            "ok": not err, "models": len(models),
                            "model": providers.STATS["by"].get(prov, {})
                            .get("model", "") if not err else "",
                            "say": (reply or {}).get("say", "") if not err
                            else "", "error": err or lerr or "",
                            "seconds": round(time.time() - t0, 1)})
        good = [r for r in results if r["ok"]]
        first = good[0] if good else None
        return {"ok": bool(good), "results": results,
                "say": first["say"] if first else "",
                "model": f"{first['name']} / {first['model']}" if first else "",
                "error": None if good else "; ".join(
                    f"{r['name']}: {r['error'][:120]}" for r in results)}

    def ai_models(self, refresh=False):
        out = []
        for prov in providers.configured(self._cfg):
            key = self._cfg.get(providers.PROVIDERS[prov]["key"])
            models, err = providers.list_models(prov, key,
                                                refresh=bool(refresh))
            out.append({"provider": prov, "models": models[:40],
                        "error": err})
        st = providers.model_status(self._cfg)
        gem = next((o for o in out if o["provider"] == "gemini"), None)
        return {"ok": any(o["models"] for o in out), "by": out,
                "models": gem["models"] if gem else [],
                "error": None if any(o["models"] for o in out) else
                "; ".join(o["error"] or "" for o in out) or "Няма ключ.",
                "working": st["working"], "chosen": st["chosen"],
                "providers": st["providers"]}

    # ---------- режисьор, състезания, села ----------

    def gm_now(self, instruction=""):
        if not self._server.ready:
            return {"ok": False, "error": "Сървърът не е готов."}
        if not self._server.online:
            return {"ok": False, "error": "Няма никой в света."}
        owner = self._cfg.get("owner_name") or None
        ok, msg = self._brain.director.think(
            "собственикът натисна бутона", instruction=(instruction or "")
            .strip() or None, admin=owner)
        return {"ok": ok, "message": msg if ok else None,
                "error": None if ok else msg}

    def boss(self, player="", preset=""):
        if not self._server.ready:
            return {"ok": False, "error": "Сървърът не е готов."}
        if not self._server.online:
            return {"ok": False, "error": "Няма никой в света."}
        spec = next((b for b in events_lib.BOSS_PRESETS
                     if b["name"] == preset), None)
        ok, msg = self._brain.engine.start_boss(
            spec, player if player in self._server.online else None)
        return {"ok": ok, "message": f"Босът {msg} се появи." if ok else None,
                "error": None if ok else msg}

    def spirit_visit(self, key="", player=""):
        if not self._server.ready:
            return {"ok": False, "error": "Сървърът не е готов."}
        ok, msg = self._brain.visits.start(key or None, player or None,
                                           reason="собственикът го прати")
        return {"ok": ok, "message": msg if ok else None,
                "error": None if ok else msg}

    def quest_cancel(self, qid):
        ok = self._brain.quests.cancel(int(qid))
        return {"ok": ok, "message": "Задачата е махната." if ok else None,
                "error": None if ok else "Няма такава задача."}

    def story_reset(self):
        self._brain.director.reset_story()
        self._brain.quests.reset()
        return {"ok": True, "message": "Историята започва отначало."}

    def fame_add(self, player, points):
        try:
            pts = int(points)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Точките са число."}
        if not re.fullmatch(r"[A-Za-z0-9_]{1,16}", player or ""):
            return {"ok": False, "error": "Избери играч."}
        self._brain.fame.add(player, max(-1000, min(pts, 1000)),
                             "от собственика")
        return {"ok": True, "message": f"{player}: "
                f"{self._brain.fame.points(player)} слава."}

    def contest(self, key):
        if not self._server.ready:
            return {"ok": False, "error": "Сървърът не е готов."}
        ok, msg = self._brain.start_event(key, source="ръчно")
        return {"ok": ok, "message": msg if ok else None,
                "error": None if ok else msg}

    def village_found(self, player):
        if not self._server.ready:
            return {"ok": False, "error": "Сървърът не е готов."}
        self._brain._refresh_positions()
        ok, msg = self._brain.villages.found_village(player)
        return {"ok": ok, "message": msg if ok else None,
                "error": None if ok else msg}

    def village_build(self, player, kind="house", style="", size="medium"):
        if not self._server.ready:
            return {"ok": False, "error": "Сървърът не е готов."}
        self._brain._refresh_positions()
        ok, msg = self._brain.villages.request_build(
            player, kind, style or None, size or None,
            owner=self._cfg.get("owner_name") or "собственика")
        return {"ok": ok, "message": msg if ok else None,
                "error": None if ok else msg}

    def worker_stop(self, wid):
        ok = self._brain.villages.stop_worker(int(wid))
        return {"ok": ok, "message": "Спря да строи." if ok else None}

    def worker_remove(self, wid):
        ok = self._brain.villages.remove_worker(int(wid))
        return {"ok": ok, "message": "Селянинът си тръгна." if ok else None}

    def villages_reset(self):
        for w in list(self._brain.villages.workers):
            self._brain.villages.remove_worker(w["id"])
        self._brain.villages.reset()
        return {"ok": True, "message": "Селата са забравени. Постройките "
                                       "остават в света."}

    # ---------- конзола: бързи действия ----------

    QUICK = {
        "op": "op {p}", "deop": "deop {p}",
        "creative": "gamemode creative {p}", "survival": "gamemode survival {p}",
        "spectator": "gamemode spectator {p}",
        "heal": "effect give {p} minecraft:instant_health 1 10 true",
        "feed": "effect give {p} minecraft:saturation 5 10 true",
        "tp_me": "tp {p} {owner}", "tp_to": "tp {owner} {p}",
        "kick": "kick {p} Изгонен от собственика",
        "day": "time set day", "night": "time set night",
        "clear": "weather clear 1200", "rain": "weather rain 1200",
        "save": "save-all", "list": "list",
        "peaceful": "difficulty peaceful", "normal": "difficulty normal",
        "keepinv_on": "gamerule keepInventory true",
        "keepinv_off": "gamerule keepInventory false",
    }

    def set_owner(self, name):
        name = (name or "").strip()
        if name and not re.fullmatch(r"[A-Za-z0-9_]{1,16}", name):
            return {"ok": False, "error": "Името е до 16 знака: латински "
                                          "букви, цифри и _."}
        self._cfg.set("owner_name", name)
        if name:
            admins = list(self._cfg.get("admins") or [])
            if name not in admins:
                admins.append(name)
                self._cfg.set("admins", admins)
                manager.write_ops(admins, offline=self._cfg.get("offline_mode"))
            wl = list(self._cfg.get("whitelist") or [])
            if name not in wl:
                wl.append(name)
                self._cfg.set("whitelist", wl)
                self._apply_whitelist()
            if self._server.running:
                self._server.command(f"op {name}")
        return {"ok": True, "message": f"Ти си {name}. Имаш OP." if name
                else "Името е изчистено."}

    def quick(self, action, player=""):
        if not self._server.running:
            return {"ok": False, "error": "Сървърът не върви."}
        tpl = self.QUICK.get(action)
        if not tpl:
            return {"ok": False, "error": "Непознато действие."}
        owner = self._cfg.get("owner_name") or ""
        if "{p}" in tpl and not re.fullmatch(r"[A-Za-z0-9_]{1,16}",
                                             player or ""):
            return {"ok": False, "error": "Избери играч."}
        if "{owner}" in tpl and not owner:
            return {"ok": False, "error": "Първо запиши твоето име в "
                                          "раздел Играчи."}
        if action in ("op", "deop"):
            want = action == "op"
            if (player in (self._cfg.get("admins") or [])) != want:
                self.toggle_admin(player)        # пази и списъка с админи
            else:
                self._server.command(f"{action} {player}")
            return {"ok": True, "message": f"{player}: OP "
                    f"{'даден' if want else 'махнат'}."}
        cmd = tpl.format(p=player, owner=owner)
        self._server.command(cmd)
        log.info("Конзола", f"> {cmd}")
        return {"ok": True, "message": f"Изпратено: {cmd}"}

    # ---------- отчет ----------

    def export_report(self):
        """Текстов файл с всичко нужно, за да се намери проблем."""
        lines = [f"BVG Craft {VERSION} {BUILT}",
                 f"Създаден: {time.strftime('%d.%m.%Y %H:%M:%S')}", ""]
        try:
            st = self._build_state()
            lines += ["== Състояние ==",
                      f"Paper: {st['paper']} ({st['mc_version']}), Java "
                      f"{st['java']} (трябва {st['java_needed']})",
                      f"Върви: {st['running']}, готов: {st['ready']}, "
                      f"онлайн: {', '.join(st['online']) or '—'}"]
            for h in st["health"]:
                lines.append(f"  {h['name']}: {h['ok']} — {h['detail']}")
            ai = st["ai"]
            lines += ["", "== AI ==",
                      f"ред: {self._cfg.get('ai_provider')}, отговаря: "
                      f"{ai['provider'] or '—'} / {ai['model'] or '—'}, днес "
                      f"{ai['today']} заявки",
                      f"недостъпни: {', '.join(ai['unavailable']) or '—'}",
                      f"успешни {ai['ok']}, грешки {ai['err']}, "
                      f"последна грешка: {ai['last_error'] or '—'}"]
            for p in ai["providers"]:
                if p["set"]:
                    lines.append(f"  {p['name']}: модели {p['models']}, "
                                 f"водещи {', '.join(p['top']) or '—'}, "
                                 f"ок {p['ok']}, грешки {p['err']}, "
                                 f"{p['last_error'] or ''}")
            story = st["story"]
            lines += ["", "== История ==",
                      f"„{story['title']}“, глава {story['chapter']}, "
                      f"цел: {story['goal']}"]
            lines += [f"  {c['text']}" for c in story["chronicle"]]
            lines += ["", "== Задачи ==",
                      json.dumps(st["quests"], ensure_ascii=False)[:2000],
                      "", "== Слава ==",
                      json.dumps(st["fame"], ensure_ascii=False)[:1000],
                      "", "== Режисьор =="]
            for d in st["gm"]["decisions"]:
                lines.append(f"  преди {d['ago']} сек ({d['source']}): "
                             f"{d['text']} -> {', '.join(d['done'])}")
            lines += ["", "== Села ==",
                      json.dumps(st["villages"], ensure_ascii=False)[:2000]]
        except Exception as e:
            lines.append(f"Състоянието не се събра: {e}")
        lines += ["", "== Отказани от сървъра команди =="]
        for t, kind, cmd, reply in list(self._sender.rejected)[-60:]:
            lines.append(f"[{time.strftime('%H:%M:%S', time.localtime(t))}] "
                         f"{kind}: {cmd[:200]}\n    -> {reply[:200]}")
        cfg = dict(self._cfg.public())
        lines += ["", "== Настройки (без ключове) ==",
                  json.dumps({k: v for k, v in cfg.items()
                              if not k.endswith("_key")}, ensure_ascii=False,
                             indent=1)[:4000]]
        lines += ["", "== Лог (последните 800 реда) =="]
        for e in log.since(0, limit=100000)[-800:]:
            lines.append(f"{e['t']} [{e['level']}] [{e['src']}] {e['msg']}")
        os.makedirs(paths.DATA, exist_ok=True)
        path = os.path.join(paths.DATA, f"otchet-{time.strftime('%Y%m%d-%H%M%S')}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        self.open_folder("data")
        return {"ok": True, "path": path,
                "message": "Отчетът е запазен в папка data. Прати ми го."}

    # ---------- пълна проверка ----------

    def diagnose(self):
        """Пробва истинските команди на живия сървър и казва кои минават."""
        checks = []

        def add(name, ok, detail=""):
            checks.append({"name": name, "ok": ok, "detail": detail})

        srv, snd = self._server, self._sender
        add("Сървърът върви", srv.running,
            "" if srv.running else "Пусни го първо.")
        if not srv.running:
            return {"ok": True, "checks": checks}
        add("Сървърът е готов", srv.ready,
            "" if srv.ready else "Още зарежда или не съм видял „Done“.")

        was = snd.enabled
        snd.enabled = True
        try:
            ok, reply = snd.query("list")
            add("RCON отговаря", ok, "" if ok else reply)
            if not ok:
                add("Паролата и портът", False,
                    "Сървърът не слуша на RCON порта. Провери в лога за "
                    "„RCON running“.")
                return {"ok": True, "checks": checks}

            import app.server.events as ev_mod
            parsed = ev_mod.parse_list(reply)
            listed = set(parsed[2]) if parsed else set()
            add("Разчитам `list`", parsed is not None,
                f"{len(listed)} в света: {', '.join(sorted(listed)) or '—'}")
            known = set(srv.online)
            add("Списъкът с играчи съвпада", listed == known,
                "" if listed == known else
                f"сървърът: {sorted(listed)}, приложението: {sorted(known)}")

            marker = f"Проверка на BVG Craft №{int(time.time()) % 1000}"
            snd.query(f"say {marker}")
            end = time.time() + 3
            seen = False
            while time.time() < end and not seen:
                seen = any(marker in ln for ln in list(srv.recent))
                time.sleep(0.2)
            add("Кирилицата от конзолата се чете", seen,
                "" if seen else "Редът не се върна със същия текст — "
                                "духовете няма да разпознават имената си.")

            self._probe_commands(snd, add, listed)
        finally:
            snd.query("kill @e[tag=bvg_probe]")
            snd.enabled = was or srv.ready

        if providers.configured(self._cfg):
            r = self.test_ai()
            for res in r["results"]:
                add(f"AI: {res['name']}", res["ok"],
                    (f"{res['model']} ({res['seconds']} сек): {res['say']}"
                     if res["ok"] else res["error"])[:200])
        else:
            add("Изкуственият разум отговаря", False, "Няма ключ.")
        return {"ok": True, "checks": checks}

    def _probe_commands(self, snd, add, listed):
        """Всяка форма на команда, която приложението праща."""
        cap = _Capture()
        world.spawn_npc(cap, "keeper", (0.5, 100.0, 0.5), 90.0)
        world.say(cap, "keeper", 'Проба с "кавички" и кирилица')
        world.announce(cap, "Проба", "подзаглавие")
        world.bubble(cap, "keeper", "Проба")
        world.actionbar(cap, "@a", "проба")
        npc = [c for c in cap.cmds if c.startswith("summon villager")]
        npc = [re.sub(r"^summon (\w+) \S+ \S+ \S+", r"summon \1 ~ ~ ~",
                      c).replace("bvg_keeper", "bvg_probe") for c in npc]
        nowhere = lambda c: re.sub(r"@a(?!\[)", "@a[tag=bvg_none]", c)

        probes = [("Призоваване на дух", npc[0] if npc else "")]
        probes += [("Реплика в чата", nowhere(c)) for c in cap.cmds
                   if c.startswith("tellraw")]
        probes += [("Надпис на екрана", nowhere(c)) for c in cap.cmds
                   if c.startswith("title @a title")][:1]
        probes += [("Балонче над духа", c) for c in cap.cmds
                   if c.startswith("execute at")][:1]
        probes += [
            ("Книга с инструкции",
             guide.give_command("@a[tag=bvg_none]")),
            ("Метеорит (падащ блок)",
             # NoGravity: пробата виси и се маха, без да падне на спауна
             'summon falling_block ~ ~45 ~ {BlockState:{Name:'
             '"minecraft:magma_block"},Time:1,DropItem:0b,HurtEntities:0b,'
             'NoGravity:1b,Tags:["bvg_probe"]}'),
            ("Гигант с мащаб",
             'summon zombie ~ ~ ~ {Tags:["bvg_probe"],'
             'PersistenceRequired:1b,Health:180f,attributes:['
             '{id:"minecraft:scale",base:3.0},{id:"minecraft:max_health",'
             'base:180},{id:"minecraft:attack_damage",base:7}]}'),
            ("Ефект върху играч",
             "effect give @a[tag=bvg_none] minecraft:levitation 3 1 true"),
            ("Частици", "particle minecraft:reverse_portal ~ ~1 ~ 3 2 3 "
                        "0.05 20"),
        ]
        probes += [
            ("Таймер на екрана (боссбар)", "bossbar add bvg:probe "
             '{"text":"проба"}'),
            ("Махане на таймера", "bossbar remove bvg:probe"),
            ("Класация (scoreboard)", "scoreboard objectives add bvgprobe "
             'minecraft.custom:minecraft.jump {"text":"проба"}'),
            ("Махане на класацията", "scoreboard objectives remove bvgprobe"),
            ("Брояч на предмети", "execute if items entity @a[tag=bvg_none] "
             "container.* minecraft:diamond"),
            ("Брояч на дървета (таг)", "execute if items entity "
             "@a[tag=bvg_none] container.* #minecraft:logs"),
            ("Награда с фойерверк", "execute at @a[tag=bvg_none] run summon "
             "firework_rocket ~ ~1 ~ {LifeTime:25,FireworksItem:{id:"
             '"minecraft:firework_rocket",count:1,components:{'
             '"minecraft:fireworks":{flight_duration:1,explosions:[{shape:'
             '"large_ball",colors:[I;16711680],has_twinkle:true}]}}}}'),
            ("Височина на земята", "execute positioned 0.5 0 0.5 positioned "
             "over motion_blocking_no_leaves run summon marker ~ ~ ~ "
             '{Tags:["bvg_probe"]}'),
            ("Празно място за строеж", "execute if blocks 0 300 0 2 302 2 "
             "0 310 0 all"),
            ("Чистене на трева", "fill 0 310 0 1 311 1 minecraft:air replace "
             "#minecraft:small_flowers"),
            ("Ранг: отбор", "team add bvg_probe_t"),
            ("Ранг: представка", 'team modify bvg_probe_t prefix '
             '{"text":"[Проба] ","color":"gold"}'),
            ("Ранг: махане", "team remove bvg_probe_t"),
            ("Слава в TAB", 'scoreboard objectives add bvgprobe2 dummy '
             '{"text":"Слава"}'),
            ("Махане на славата", "scoreboard objectives remove bvgprobe2"),
            ("Бос (атрибути, броня)", "summon zombie ~ ~ ~ " +
             events_lib.boss_nbt(events_lib.boss_spec(
                 events_lib.BOSS_PRESETS[0])).replace("bvg_boss",
                                                      "bvg_probe")),
            ("Търговец с истински сделки", "summon villager ~ ~ ~ "
             '{Tags:["bvg_probe"],NoAI:1b,Offers:{Recipes:[' +
             world.recipes([("emerald 2", "diamond 1")]) + "]}}"),
            ("Селянин тръгва да се разхожда",
             "data merge entity @e[tag=bvg_probe,type=villager,limit=1] "
             "{NoAI:0b}"),
            ("Задача: статистика за убити", "scoreboard objectives add "
             "bvgprobe3 minecraft.killed:minecraft.zombie"),
            ("Махане на статистиката", "scoreboard objectives remove "
             "bvgprobe3"),
            ("Книга-летопис", guide.book_command(
                "@a[tag=bvg_none]", "Летопис на BVG WORLD", "Режисьорът",
                ["Проба", "Втора страница"])),
        ]
        for label, command in probes:
            if not command:
                add(label, False, "Не успях да сглобя командата.")
                continue
            ok, reply = snd.query(command)
            bad = (not ok) or classify_reply(reply) == "syntax"
            add(label, not bad, (reply or "")[:160] if bad else "")

        ok, reply = snd.query("execute if entity @e[tag=bvg_probe]")
        add("Засичане на същество", ok and "passed" in (reply or "").lower(),
            reply or "")

        who = sorted(listed)[0] if listed else None
        if who:
            pos = world.player_pos(snd, who)
            add(f"Позиция на играч ({who})", pos is not None,
                f"X {pos[0]:.0f} Y {pos[1]:.0f} Z {pos[2]:.0f}" if pos
                else "Не успях да я прочета — героите няма да знаят къде си.")
        else:
            add("Позиция на играч", None, "Няма играч в света за проба.")

    # ---------- разни ----------

    def open_url(self, prov):
        """Страницата за ключ на услугата — в обикновения браузър."""
        info = providers.PROVIDERS.get(prov)
        if not info:
            return {"ok": False, "error": "Няма такава услуга."}
        import webbrowser
        try:
            webbrowser.open(info["url"])
        except Exception as e:
            return {"ok": False, "error": f"Отвори ръчно: {info['url']} ({e})"}
        return {"ok": True, "message": f"Отварям {info['url']}"}

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

    def _lan_ip(self):
        if self._ip_cache is None:
            import socket
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                self._ip_cache = s.getsockname()[0]
                s.close()
            except Exception:
                self._ip_cache = "127.0.0.1"
        return self._ip_cache

    def _shutdown(self):
        self._monitor.stop()
        self._brain.stop()
        if self._server.running:
            self._server.stop(wait=45)
