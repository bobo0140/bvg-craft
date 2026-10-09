"""
rcon.py — връзка към сървъра за изпращане на команди.

Важно за RCON на Minecraft (видяно на истински сървър):
  - сървърът чете по ЕДИН пакет и до 1460 байта наведнъж. Ако пратиш
    две команди, без да чакаш отговора на първата, или една команда над
    ~1400 байта (кирилицата е по 2 байта на буква), сървърът просто
    затваря връзката и командите се губят;
  - затова тук всяка команда чака своя отговор, а дългите (книгата с
    инструкции, големи надписи) минават през конзолата на сървъра.

Има две отделни връзки: една за опашката с команди и една за въпроси
(кой е онлайн, къде е играчът), за да не чака едното другото.
"""

import collections
import queue
import socket
import struct
import threading
import time

from ..logbus import log

T_AUTH, T_CMD = 3, 2
MAX_BYTES = 1400              # над това — през конзолата
FRAGMENT = 4096               # сървърът реже дългите отговори на толкова

# Командата е написана грешно — това е бъг и трябва да се види
SYNTAX_WORDS = ("<--[here]", "unknown or incomplete command",
                "incorrect argument", "unknown item", "unknown block",
                "unknown entity", "unknown effect", "unknown particle",
                "malformed", "can't find element", "invalid ",
                "unknown attribute", "unknown criterion")
# Командата е вярна, но не е успяла (място извън света, няма място...)
FAIL_WORDS = ("cannot", "can't", "unable to", "could not", "failed",
              "is not allowed", "not loaded", "too many blocks",
              "outside of the world", "is too big", "error executing")
# Няма кого да засегне — нормално, не е грешка
NOBODY = ("no entity was found", "no player was found",
          "no targets matched", "no entities", "test failed")


def classify_reply(reply):
    """'ok' | 'syntax' | 'fail' — какво значи отговорът на сървъра."""
    low = (reply or "").lower()
    if not low:
        return "ok"
    if any(w in low for w in SYNTAX_WORDS):
        return "syntax"
    if any(w in low for w in NOBODY):
        return "ok"
    if any(w in low for w in FAIL_WORDS):
        return "fail"
    return "ok"


def too_long(cmd):
    return len(cmd.encode("utf-8")) > MAX_BYTES


class RconError(Exception):
    pass


class Rcon:
    """Една връзка: команда -> отговор, после следващата."""

    def __init__(self, host, port, password, timeout=10):
        self.host, self.port, self.password = host, int(port), password
        self.timeout = timeout
        self.sock = None
        self._id = 0
        self._lock = threading.Lock()

    def _connect(self):
        self.sock = socket.create_connection((self.host, self.port),
                                             timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
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
        self._id = self._id % 2_000_000_000 + 1
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
        if length < 10 or length > 1_000_000:
            raise RconError(f"странен пакет ({length} байта)")
        raw = self._recv(length)
        pid, ptype = struct.unpack("<ii", raw[:8])
        return pid, ptype, raw[8:-2].decode("utf-8", errors="replace")

    def _response(self, pid):
        """Отговорът на командата; дългите идват на парчета по 4096 знака,
        без знак за край — затова след пълно парче чакаме кратко за още."""
        parts = []
        try:
            while True:
                rid, _, body = self._read()
                if rid != pid:
                    continue                # остатък от нещо старо
                parts.append(body)
                if len(body) < FRAGMENT:
                    break
                self.sock.settimeout(0.4)
        except socket.timeout:
            if not parts:
                raise
        finally:
            if self.sock:
                self.sock.settimeout(self.timeout)
        return "".join(parts)

    def run_many(self, cmds):
        """Праща командите една по една. Връща [(команда, отговор)]."""
        with self._lock:
            if not self.sock:
                self._connect()
            out = []
            for c in cmds:
                pid = self._send(T_CMD, c)
                out.append((c, self._response(pid)))
            return out

    def run(self, cmd):
        return self.run_many([cmd])[0][1]


class Sender:
    """Опашка от команди, която се праща отзад, и въпроси с отговор."""

    MAX_QUEUE = 6000

    def __init__(self, cfg):
        self.cfg = cfg
        self.q = queue.Queue()
        self._cmd = None             # връзката на опашката
        self._ask = None             # връзката за въпроси
        self.console = None          # server.command — за дългите команди
        self.errors = 0
        self.enabled = False
        self.last_ok = 0.0          # кога последно има успешен обмен
        self.last_error = ""
        self.fail_streak = 0
        self.sent = 0
        # Последните отказани команди: (време, вид, команда, отговор)
        self.rejected = collections.deque(maxlen=200)
        self._clock = threading.Lock()
        threading.Thread(target=self._worker, daemon=True).start()

    # ---------- връзки ----------

    def _new(self):
        return Rcon("127.0.0.1", self.cfg.get("rcon_port"),
                    self.cfg.get("rcon_password"))

    def _client(self, which="ask"):
        with self._clock:
            if which == "cmd":
                if self._cmd is None:
                    self._cmd = self._new()
                return self._cmd
            if self._ask is None:
                self._ask = self._new()
            return self._ask

    def _drop(self, which="ask"):
        with self._clock:
            c = self._cmd if which == "cmd" else self._ask
            if c:
                c.close()
            if which == "cmd":
                self._cmd = None
            else:
                self._ask = None

    # стари имена, ползвани отвън
    @property
    def rcon(self):
        return self._ask

    # ---------- опашка ----------

    def send(self, command, optional=False):
        """optional=True е украса, която може да отпадне при претоварване."""
        if not command:
            return
        if optional and self.q.qsize() > self.MAX_QUEUE // 2:
            return
        if self.q.qsize() > self.MAX_QUEUE:
            return
        self.q.put(command)

    def send_many(self, commands, optional=False):
        for c in commands:
            self.send(c, optional=optional)

    def _via_console(self, cmd):
        if self.console:
            self.console(cmd)
            return True
        return False

    # ---------- въпроси ----------

    def query(self, command):
        """Синхронна команда с отговор. (успех, отговор)"""
        return self.query_many([command])[0] if command else (False, "")

    def query_many(self, commands):
        """Много въпроси един след друг: [(успех, отговор), ...]."""
        commands = [c for c in commands if c]
        if not commands:
            return []
        if not self.enabled:
            return [(False, "сървърът не върви")] * len(commands)
        out = []
        short = []
        for c in commands:
            if too_long(c):
                ok = self._via_console(c)
                out.append((ok, "" if ok else "командата е твърде дълга"))
            else:
                out.append(None)
                short.append(c)
        if short:
            try:
                res = self._client("ask").run_many(short)
                self.last_ok, self.fail_streak = time.time(), 0
                it = iter([(True, r) for _, r in res])
            except Exception as e:
                self._drop("ask")
                self.last_error = f"{type(e).__name__}: {e}"
                it = iter([(False, self.last_error)] * len(short))
            out = [o if o is not None else next(it) for o in out]
        return out

    # ---------- отзад ----------

    def _check(self, cmd, reply):
        self.sent += 1
        kind = classify_reply(reply)
        if kind == "ok":
            return
        self.rejected.append((time.time(), kind, cmd[:300],
                              (reply or "").strip()[:300]))
        if kind == "syntax":
            self.errors += 1
            if self.errors <= 30:
                log.warn("RCON", f"Сървърът не разбра: "
                                 f"{cmd.split('{')[0][:70]} — "
                                 f"{reply.strip()[:160]}")

    def _worker(self):
        while True:
            first = self.q.get()
            batch = [first]
            while len(batch) < 200:
                try:
                    batch.append(self.q.get_nowait())
                except queue.Empty:
                    break
            if not self.enabled:
                time.sleep(0.5)
                continue
            done = 0
            try:
                client = self._client("cmd")
                for c in batch:
                    if too_long(c):
                        self._via_console(c)
                    else:
                        _, r = client.run_many([c])[0]
                        self._check(c, r)
                    done += 1
                self.last_ok, self.fail_streak = time.time(), 0
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                self.fail_streak += 1
                self._drop("cmd")
                rest = batch[done + 1:]      # тази, на която падна, — пропускаме
                if done < len(batch):
                    self.rejected.append((time.time(), "fail",
                                          batch[done][:300], self.last_error))
                if self.fail_streak >= 4:
                    log.error("RCON", f"Не мога да говоря със сървъра "
                                      f"({self.last_error}). Пропускам "
                                      f"{len(rest)} команди.")
                    self.fail_streak = 0
                    time.sleep(3)
                    continue
                log.warn("RCON", f"Връзката падна: {e}. Продължавам "
                                 f"(опит {self.fail_streak}/4).")
                for c in rest:
                    self.q.put(c)
                time.sleep(1.0)
