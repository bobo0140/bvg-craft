"""
providers.py — връзката с езиковите модели.

Gemini е по подразбиране: безплатен е, без карта. OpenAI е резерва.
Има ограничител на заявките, защото с много играчи безплатният лимит
се изчерпва за минути, ако всеки ред в чата отиде при модела.
"""

import base64
import json
import re
import threading
import time
from collections import deque

import requests

from ..logbus import log

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
              "{model}:generateContent")

FALLBACKS = {
    "gemini": ["gemini-2.5-flash", "gemini-2.5-flash-lite",
               "gemini-2.0-flash"],
    "openai": ["gpt-4o-mini", "gpt-4.1-mini"],
}


class RateLimiter:
    """Не повече от N заявки в минута — отвъд това отказваме веднага."""

    def __init__(self, per_minute=8):
        self.per_minute = per_minute
        self.stamps = deque()
        self._lock = threading.Lock()
        self.cooldown_until = 0.0

    def allow(self) -> bool:
        now = time.time()
        with self._lock:
            if now < self.cooldown_until:
                return False
            while self.stamps and now - self.stamps[0] > 60:
                self.stamps.popleft()
            if len(self.stamps) >= self.per_minute:
                return False
            self.stamps.append(now)
            return True

    def back_off(self, seconds):
        with self._lock:
            self.cooldown_until = max(self.cooldown_until,
                                      time.time() + seconds)


limiter = RateLimiter()

# Какво е станало с последните заявки — за да се вижда защо AI-то мълчи
STATS = {"ok": 0, "err": 0, "busy": 0, "last_ok": 0.0, "last_error": "",
         "last_error_at": 0.0}


def _note_ok():
    STATS["ok"] += 1
    STATS["last_ok"] = time.time()


def _note_err(msg):
    STATS["err"] += 1
    STATS["last_error"] = msg
    STATS["last_error_at"] = time.time()


def _api_error(r) -> str:
    try:
        err = r.json().get("error") or {}
        msg = err.get("message") or ""
        code = err.get("code") or err.get("status") or err.get("type") or ""
        return f"{msg} [{code}]" if code else (msg or r.text[:160])
    except Exception:
        return (r.text or "")[:160]


def _classify(r, model, who):
    """None ако е наред; иначе (може_да_се_опита_пак, текст)."""
    if r.status_code < 400:
        return None
    d = _api_error(r)
    if r.status_code in (401, 403):
        return False, f"Ключът е отхвърлен от {who}: {d}"
    if r.status_code == 404:
        return False, f"Няма модел {model}: {d}"
    if r.status_code == 429:
        low = d.lower().replace(" ", "")
        if who == "Gemini":
            # Безплатният тир има лимит на минута и лимит на ден
            if "perday" in low:
                limiter.back_off(600)
                return False, ("Дневният безплатен лимит на Gemini свърши. "
                               "Нулира се през нощта. Услугата е наред.")
            limiter.back_off(20)
            return True, f"Твърде много заявки в минута към Gemini. ({d})"
        if any(w in low for w in ("credit", "billing", "insufficient")):
            return False, (f"Нямаш кредити в {who}. Превключи на Gemini — "
                           f"безплатен е. ({d})")
        limiter.back_off(30)
        return True, f"Лимит на заявките ({who}): {d}"
    if r.status_code >= 500:
        return True, f"{who} е претоварен в момента: {d}"
    return False, f"Грешка {r.status_code} от {who}: {d}"


def _parse_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {"say": str(data)}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return {"say": text[:300]}


def _gemini(key, model, system, messages, audio, timeout):
    contents = []
    for m in messages:
        contents.append({"role": "model" if m["role"] == "assistant"
                         else "user", "parts": [{"text": m["content"]}]})
    if audio:
        contents[-1]["parts"].insert(0, {"inline_data": {
            "mime_type": "audio/wav",
            "data": base64.b64encode(audio).decode("ascii")}})
    r = requests.post(
        GEMINI_URL.format(model=model), params={"key": key}, timeout=timeout,
        json={"contents": contents,
              "systemInstruction": {"parts": [{"text": system}]},
              "generationConfig": {"temperature": 0.9,
                                   "responseMimeType": "application/json"}})
    bad = _classify(r, model, "Gemini")
    if bad:
        return None, bad
    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts), None
    except (KeyError, IndexError):
        return None, (True, "Моделът не върна отговор.")


def _openai(key, model, system, messages, audio, timeout):
    msgs = [{"role": "system", "content": system}] + messages
    r = requests.post(
        OPENAI_URL, timeout=timeout,
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": msgs, "temperature": 0.9,
              "response_format": {"type": "json_object"}})
    bad = _classify(r, model, "OpenAI")
    if bad:
        return None, bad
    return r.json()["choices"][0]["message"]["content"], None


def ask(cfg, system, messages, audio=None, timeout=45, force=False):
    """Пита модела. Връща (речник с отговор, грешка).

    force=True минава покрай ограничителя — за неща, които администраторът
    поиска изрично.
    """
    provider = cfg.get("ai_provider") or "gemini"
    key = cfg.get("gemini_key" if provider == "gemini" else "openai_key")
    if not key:
        _note_err("Няма API ключ.")
        return None, "Няма API ключ."
    if not force and not limiter.allow():
        STATS["busy"] += 1
        return None, "busy"

    first = cfg.get("gemini_model" if provider == "gemini"
                    else "openai_model")
    chain = [first] + [m for m in FALLBACKS[provider] if m != first]
    call = _gemini if provider == "gemini" else _openai
    last = "неизвестна грешка"

    for i, model in enumerate(chain):
        for attempt in range(2):
            try:
                text, bad = call(key, model, system, messages, audio, timeout)
            except requests.exceptions.Timeout:
                text, bad = None, (True, "Отговорът се забави.")
            except Exception as e:
                text, bad = None, (False, f"{type(e).__name__}: {e}")
            if not bad:
                if model != first:
                    log.info("AI", f"{first} беше зает, отговори {model}.")
                _note_ok()
                return _parse_json(text), None
            retry, last = bad
            if not retry:
                if "Няма модел" in last and i < len(chain) - 1:
                    break                   # този модел го няма — следващият
                _note_err(last)
                return None, last
            time.sleep(1.2 + i)
    last = last + " (всички модели са заети)"
    _note_err(last)
    return None, last
