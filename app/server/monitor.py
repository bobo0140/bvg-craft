"""
monitor.py — пита сървъра кой е в света, вместо да гадае от лога.

Редът „joined the game" може да се изгуби (цветове, кодиране, друг
формат), а тогава списъкът е празен, събитията са изключени и духовете
мислят, че никой го няма. `list` през RCON е източникът на истината;
логът само ускорява реакцията.

Служи и за резерва на „готов е": ако редът „Done" е изпуснат, но
сървърът отговаря на RCON, значи е готов.
"""

import threading
import time

from ..logbus import log
from . import events

POLL = 3.0
DEBOUNCE = 6.0      # толкова секунди след събитие от лога не го оспорваме


class Monitor:
    def __init__(self, server, sender, on_ready, on_event):
        self.server = server
        self.sender = sender
        self.on_ready = on_ready
        self.on_event = on_event
        self._stop = threading.Event()
        self._seen_in = {}       # име -> колко пъти поред е във `list`
        self._seen_out = {}      # име -> колко пъти поред липсва
        self.last_list = None    # (време, брой, максимум, имена)
        self.rcon_ok = False
        self.polls = 0

    def start(self):
        self._stop.clear()
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(POLL):
            try:
                self.tick()
            except Exception as e:
                log.error("Монитор", f"{type(e).__name__}: {e}")

    def tick(self):
        srv = self.server
        if not srv.running:
            self.rcon_ok = False
            self._seen_in.clear()
            self._seen_out.clear()
            return

        # Преди „готов" питаме по-рядко: сървърът още зарежда
        if not srv.ready and srv.last_line and \
                time.time() - srv.last_line < 2:
            return

        self.sender.enabled = True if srv.ready else self.sender.enabled
        ok, reply = self._ask()
        self.rcon_ok = ok
        if not ok:
            return
        parsed = events.parse_list(reply)
        if parsed is None:
            return
        self.polls += 1
        count, maximum, names = parsed
        self.last_list = (time.time(), count, maximum, names)

        if not srv.ready:
            log.ok("Монитор", "Сървърът отговаря на RCON, макар да не видях "
                              "реда „Done“. Смятам го за готов.")
            srv.apply({"type": "ready"})
            self.on_ready()
        self._reconcile(set(names))

    def _ask(self):
        was = self.sender.enabled
        self.sender.enabled = True
        try:
            return self.sender.query("list")
        finally:
            self.sender.enabled = was or self.server.ready

    def _reconcile(self, listed):
        srv, now = self.server, time.time()
        for name in list(listed):
            self._seen_out.pop(name, None)
            if name in srv.online:
                self._seen_in.pop(name, None)
                continue
            if now - srv.left_at.get(name, 0) < DEBOUNCE:
                continue         # току-що е излязъл; `list` е позастаряла
            self._seen_in[name] = self._seen_in.get(name, 0) + 1
            if self._seen_in[name] >= 2:
                self._seen_in.pop(name, None)
                log.warn("Монитор", f"{name} е в света, но не видях "
                                    f"влизането му в лога. Добавям го.")
                ev = {"type": "join", "player": name, "synthetic": True}
                srv.apply(ev)
                self.on_event(ev)

        for name in list(srv.online):
            if name in listed:
                continue
            if now - srv.joined_at.get(name, 0) < DEBOUNCE:
                continue
            self._seen_out[name] = self._seen_out.get(name, 0) + 1
            if self._seen_out[name] >= 2:
                self._seen_out.pop(name, None)
                log.warn("Монитор", f"{name} не е в света, а го водех за "
                                    f"онлайн. Махам го.")
                ev = {"type": "leave", "player": name, "synthetic": True}
                srv.apply(ev)
                self.on_event(ev)
