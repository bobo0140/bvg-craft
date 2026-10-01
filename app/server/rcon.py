"""
rcon.py — връзка към сървъра за изпращане на команди.

Държи една постоянна връзка и праща партиди, без да чака отговор след
всяка команда. Ако връзката падне насред партида, неизпратените се
връщат и тръгват отново — така постройка никога не остава наполовина.
"""

import queue
import socket
import struct
import threading
import time

from ..logbus import log

T_AUTH, T_CMD = 3, 2
BAD_WORDS = ("unknown or incomplete", "unknown command", "expected",
             "invalid", "incorrect argument", "no entity was found",
             "cannot", "is not allowed", "unable to")


class RconError(Exception):
    pass


class Rcon:
    def __init__(self, host, port, password, timeout=8):
        self.host, self.port, self.password = host, int(port), password
        self.timeout = timeout
        self.sock = None
        self._id = 0
        self._lock = threading.Lock()

    def _connect(self):
        self.sock = socket.create_connection((self.host, self.port),
                                             timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self._id = 0
        self._send(T_AUTH, self.password)
        pid, _, _ = self._read()
        if pid == -1:
            self.close()
            raise RconError("RCON паролата е грешна")

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def _send(self, ptype, body):
        self._id += 1
        data = body.encode("utf-8")
        payload = struct.pack("<ii", self._id, ptype) + data + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(payload)) + payload)
        return self._id

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RconError("връзката се затвори")
            buf += chunk
        return buf

    def _read(self):
        length = struct.unpack("<i", self._recv(4))[0]
        raw = self._recv(length)
        pid, ptype = struct.unpack("<ii", raw[:8])
        return pid, ptype, raw[8:-2].decode("utf-8", errors="replace")

    def run_many(self, cmds, batch=40):
        """Праща много команди. Връща [(команда, отговор)] по ред."""
        with self._lock:
            if not self.sock:
                self._connect()
            out = []
            for i in range(0, len(cmds), batch):
                chunk = cmds[i:i + batch]
                ids = [self._send(T_CMD, c) for c in chunk]
                got, seen = {}, 0
                while seen < len(ids):
                    pid, _, body = self._read()
                    if pid in got:
                        got[pid] += body
                    else:
                        got[pid] = body
                        seen += 1
                out.extend((c, got.get(p, "")) for c, p in zip(chunk, ids))
            return out

    def run(self, cmd):
        return self.run_many([cmd])[0][1]


class Sender:
    """Опашка от команди, която се праща отзад."""

    MAX_QUEUE = 6000

    def __init__(self, cfg):
        self.cfg = cfg
        self.q = queue.Queue()
        self.rcon = None
        self.errors = 0
        self.enabled = False
        threading.Thread(target=self._worker, daemon=True).start()

    def _client(self):
        if self.rcon is None:
            self.rcon = Rcon("127.0.0.1", self.cfg.get("rcon_port"),
                             self.cfg.get("rcon_password"))
        return self.rcon

    def send(self, command, optional=False):
        """optional=True е украса, която може да отпадне при претоварване."""
        if not command:
            return
        if optional and self.q.qsize() > self.MAX_QUEUE // 2:
            return
        self.q.put(command)

    def send_many(self, commands, optional=False):
        for c in commands:
            self.send(c, optional=optional)

    def query(self, command):
        """Синхронна команда с отговор. (успех, отговор)"""
        if not self.enabled:
            return False, "сървърът не върви"
        try:
            return True, self._client().run(command)
        except Exception as e:
            self._drop()
            return False, f"{type(e).__name__}: {e}"

    def _drop(self):
        if self.rcon:
            self.rcon.close()
        self.rcon = None

    def _check(self, cmd, reply):
        low = (reply or "").lower()
        if any(w in low for w in BAD_WORDS):
            self.errors += 1
            if self.errors <= 20:
                log.warn("RCON", f"Отказано: {cmd.split('{')[0][:70]} — "
                                 f"{reply.strip()[:140]}")

    def _worker(self):
        while True:
            first = self.q.get()
            batch = [first]
            while len(batch) < 300:
                try:
                    batch.append(self.q.get_nowait())
                except queue.Empty:
                    break
            if not self.enabled:
                time.sleep(0.5)
                continue
            try:
                for c, r in self._client().run_many(batch):
                    self._check(c, r)
            except Exception as e:
                log.warn("RCON", f"Връзката падна: {e}. Пробвам пак.")
                self._drop()
                for c in batch:
                    self.q.put(c)
                time.sleep(1.5)
