"""Логът — към файла и към интерфейса едновременно."""
import collections
import threading
import time

from . import paths


class LogBus:
    def __init__(self):
        self._lock = threading.Lock()
        self.buffer = collections.deque(maxlen=800)
        self.listeners = []
        self.seq = 0

    def emit(self, level, source, message):
        with self._lock:
            self.seq += 1
            entry = {"id": self.seq, "t": time.strftime("%H:%M:%S"),
                     "level": level, "src": source, "msg": str(message)}
            self.buffer.append(entry)
            try:
                with open(paths.LOG, "a", encoding="utf-8") as f:
                    f.write(f"{entry['t']} [{level}] [{source}] {message}\n")
            except OSError:
                pass
        for fn in list(self.listeners):
            try:
                fn(entry)
            except Exception:
                pass

    def info(self, src, msg):
        self.emit("info", src, msg)

    def ok(self, src, msg):
        self.emit("ok", src, msg)

    def warn(self, src, msg):
        self.emit("warn", src, msg)

    def error(self, src, msg):
        self.emit("error", src, msg)

    def console(self, msg):
        self.emit("console", "MC", msg)

    def recent(self, n=300):
        with self._lock:
            return list(self.buffer)[-n:]

    def since(self, last_id, limit=400):
        """Редовете след даден номер. Номерата растат винаги, така че
        въртенето на буфера не губи нищо и не повтаря."""
        with self._lock:
            return [e for e in self.buffer if e["id"] > last_id][:limit]


log = LogBus()
