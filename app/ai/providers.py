"""
providers.py — връзката с езиковите модели.

Gemini е по подразбиране: безплатен е, без карта. OpenAI е резерва.

Моделите идват и си отиват (gemini-2.0-flash спря на 1 юни 2026, а 2.5
вече не се дават на нови ключове). Затова не разчитаме на твърд списък:
питаме услугата кои модели има за този ключ, подреждаме ги и при всяка
грешка минаваме на следващия. Модел, който го няма или е изчерпал
дневния си лимит, се помни и не се пробва отново известно време.
"""

import base64
import hashlib
import json
import re
import threading
import time
from collections import deque

import requests

from ..logbus import log

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
OPENAI_BASE = "https://api.openai.com/v1"

# Ако списъкът не може да се вземе (няма интернет, временна грешка),
# пробваме тези поред. Псевдонимите *-latest Google ги държи живи.
GEMINI_GUESS = ["gemini-flash-latest", "gemini-3.8-flash", "gemini-3.7-flash",
                "gemini-3.6-flash", "gemini-3.5-flash",
                "gemini-flash-lite-latest", "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite", "gemini-2.5-flash",
                "gemini-2.5-flash-lite"]
OPENAI_GUESS = ["gpt-4.1-mini", "gpt-4o-mini", "gpt-5-mini", "gpt-5-nano"]

# Модели, които не са за чат (картинки, глас, вграждане и т.н.)
NOT_CHAT = ("tts", "image", "live", "embedding", "audio", "transcribe",
            "robotics", "computer-use", "omni", "veo", "imagen", "lyria",
            "aqa", "learnlm", "banana", "native", "translate", "research",
            "antigravity", "customtools", "gemma", "realtime", "search",
            "instruct", "moderation", "whisper", "dall-e", "codex", "sora",
            "babbage", "davinci", "transcribe", "chat-latest")

CHAIN_LEN = 6            # колко модела пробваме за една заявка
LIST_TTL = 6 * 3600      # колко време важи списъкът с модели


# ---------------------------------------------------------------------------
# Ограничител: защита от спам в чата, не от лимитите на услугата
# ---------------------------------------------------------------------------

class RateLimiter:
    """Не повече от N заявки в минута — отвъд това отказваме веднага."""

    def __init__(self, per_minute=20):
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
         "last_error_at": 0.0, "model": "", "models": [], "provider": ""}

_lock = threading.Lock()
_catalog = {}        # (доставчик, хеш на ключа) -> (кога, [модели], грешка)
_dead = {}           # модел -> до кога не го пробваме (няма го / изчерпан)
_cool = {}           # модел -> до кога е зает (лимит в минута, претоварен)
_plain = set()       # модели, които не приемат допълнителни настройки


def _note_ok(model=""):
    STATS["ok"] += 1
    STATS["last_ok"] = time.time()
    if model:
        STATS["model"] = model


def _note_err(msg):
    STATS["err"] += 1
    STATS["last_error"] = msg
    STATS["last_error_at"] = time.time()


def _khash(key):
    return hashlib.sha1((key or "").encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# Списък с модели
# ---------------------------------------------------------------------------

def _is_chat(mid):
    low = mid.lower()
    return not any(w in low for w in NOT_CHAT)


def _gemini_rank(mid):
    """По-малко = по-добре: стабилен flash, после lite, после pro."""
    low = mid.lower()
    preview = ("preview" in low or "exp" in low) and not low.endswith("latest")
    m = re.match(r"gemini-(\d+(?:\.\d+)?)-(flash|pro)(-lite)?", low)
    if m:
        ver = float(m.group(1))
        tier = 2 if m.group(2) == "pro" else (1 if m.group(3) else 0)
        dated = bool(re.search(r"-\d{3}$|-\d{2}-\d{4}$", low))
        # 2.x вече не се дават на нови ключове (2.0 спря изобщо) — накрая
        return (preview, ver < 3, tier, -ver, dated, low)
    if low == "gemini-flash-latest":
        return (False, False, 0, 1.0, False, low)   # след истинските flash
    if low == "gemini-flash-lite-latest":
        return (False, False, 1, 1.0, False, low)
    if low == "gemini-pro-latest":
        return (False, False, 2, 1.0, False, low)
    return (True, True, 3, 0.0, True, low)


def _openai_rank(mid):
    low = mid.lower()
    m = re.match(r"gpt-(\d+(?:\.\d+)?)(o)?(?:-(mini|nano))?", low)
    if not m:
        return (9, 0.0, low)
    ver = float(m.group(1))
    size = m.group(3)
    tier = 0 if size == "mini" else 1 if size == "nano" else 2
    dated = bool(re.search(r"-\d{4}-\d{2}-\d{2}$", low))
    return (tier, -ver, dated, low)


def _fetch_gemini(key):
    out, token = [], None
    for _ in range(10):
        params = {"key": key, "pageSize": 200}
        if token:
            params["pageToken"] = token
        r = requests.get(f"{GEMINI_BASE}/models", params=params, timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(_api_error(r))
        data = r.json()
        for m in data.get("models", []):
            methods = m.get("supportedGenerationMethods") or \
                m.get("supportedActions") or []
            if "generateContent" not in methods:
                continue
            mid = (m.get("name") or "").split("/", 1)[-1]
            if mid.startswith("gemini") and _is_chat(mid):
                out.append(mid)
        token = data.get("nextPageToken")
        if not token:
            break
    return sorted(set(out), key=_gemini_rank)


def _fetch_openai(key):
    r = requests.get(f"{OPENAI_BASE}/models", timeout=20,
                     headers={"Authorization": f"Bearer {key}"})
    if r.status_code >= 400:
        raise RuntimeError(_api_error(r))
    ids = [m.get("id", "") for m in r.json().get("data", [])]
    ok = [i for i in ids if i.startswith("gpt-") and _is_chat(i)]
    return sorted(set(ok), key=_openai_rank)


def list_models(provider, key, refresh=False):
    """(модели, грешка). Кешира се за няколко часа."""
    if not key:
        return [], "Няма ключ."
    ck = (provider, _khash(key))
    now = time.time()
    with _lock:
        hit = _catalog.get(ck)
    if hit and not refresh and now - hit[0] < LIST_TTL and hit[1]:
        return hit[1], hit[2]
    try:
        models = (_fetch_gemini if provider == "gemini"
                  else _fetch_openai)(key)
        err = None if models else "Услугата не върна нито един модел за чат."
    except Exception as e:
        models, err = [], f"{type(e).__name__}: {e}"
    with _lock:
        if models or not hit:
            _catalog[ck] = (now, models, err)
    if models:
        STATS["models"] = models[:20]
        STATS["provider"] = provider
        log.info("AI", f"Налични модели ({provider}): "
                       f"{', '.join(models[:6])}" +
                       (" …" if len(models) > 6 else ""))
    elif err:
        log.warn("AI", f"Не мога да взема списъка с модели: {err}")
    return models, err


def _chain(cfg, provider, key):
    models, _ = list_models(provider, key)
    base = models or (GEMINI_GUESS if provider == "gemini" else OPENAI_GUESS)
    want = (cfg.get("gemini_model" if provider == "gemini"
                    else "openai_model") or "auto").strip()
    if want and want != "auto":
        # Ръчно избран модел — първи, ако съществува (или ако не знаем)
        if not models or want in models:
            base = [want] + [m for m in base if m != want]
    now = time.time()
    alive = [m for m in base if _dead.get(m, 0) <= now]
    fresh = [m for m in alive if _cool.get(m, 0) <= now]
    busy = [m for m in alive if _cool.get(m, 0) > now]
    chain = (fresh + busy)[:CHAIN_LEN]
    if not chain and base:
        # Всичко е отписано — по-добре пак да опитаме, отколкото да мълчим
        chain = base[:2]
    return chain


def model_status(cfg):
    """За интерфейса: какви модели има и кой отговаря."""
    provider = cfg.get("ai_provider") or "gemini"
    key = cfg.get("gemini_key" if provider == "gemini" else "openai_key")
    with _lock:
        hit = _catalog.get((provider, _khash(key or "")))
    models = hit[1] if hit else []
    now = time.time()
    return {"provider": provider, "models": models[:25],
            "working": STATS.get("model") or "",
            "chosen": cfg.get("gemini_model" if provider == "gemini"
                              else "openai_model") or "auto",
            "unavailable": sorted(m for m, t in _dead.items() if t > now),
            "busy": sorted(m for m, t in _cool.items() if t > now),
            "list_error": hit[2] if hit else None}


def forget_models():
    """След смяна на ключа — наново."""
    with _lock:
        _catalog.clear()
    _dead.clear()
    _cool.clear()
    _plain.clear()
    STATS["model"] = ""


# ---------------------------------------------------------------------------
# Грешки
# ---------------------------------------------------------------------------

def _api_error(r) -> str:
    try:
        err = r.json().get("error") or {}
        if isinstance(err, str):
            return err[:300]
        msg = err.get("message") or ""
        code = err.get("status") or err.get("code") or err.get("type") or ""
        return f"{msg} [{code}]" if code else (msg or r.text[:200])
    except Exception:
        return (r.text or "")[:200]


def _seconds_to_quota_reset():
    """Безплатните дневни лимити се нулират в полунощ тихоокеанско време."""
    now = time.gmtime()
    # 08:00 UTC ≈ полунощ в Калифорния (зимно време); лятно е 07:00
    target = 8 * 3600
    cur = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
    left = (target - cur) % 86400
    return max(600, left)


class _Fail(Exception):
    """kind: key | gone | day | busy | bad | extras"""

    def __init__(self, kind, text):
        super().__init__(text)
        self.kind, self.text = kind, text


def _classify(r, model, who):
    if r.status_code < 400:
        return None
    d = _api_error(r)
    low = d.lower()
    flat = low.replace(" ", "")
    if r.status_code == 401 or "api_key_invalid" in flat or \
            "apikeynotvalid" in flat or "incorrectapikey" in flat or \
            "invalid_api_key" in flat:
        return _Fail("key", f"Ключът не е приет от {who}: {d}")
    if r.status_code == 404 or any(w in low for w in (
            "not found", "is not supported", "no longer available",
            "not available", "has been deprecated", "does not exist",
            "do not have access", "does not have access")):
        return _Fail("gone", f"Моделът {model} не е достъпен: {d}")
    if r.status_code == 403:
        if "permission" in low and "model" in low:
            return _Fail("gone", f"Моделът {model} не е разрешен: {d}")
        return _Fail("key", f"{who} отказа достъп: {d}")
    if r.status_code == 429:
        if "limit:0" in flat or "limit: 0" in low:
            return _Fail("gone", f"{model} няма безплатен лимит: {d}")
        if "perday" in flat or "per_day" in flat or "daily" in low:
            return _Fail("day", f"Дневният лимит на {model} свърши.")
        if any(w in flat for w in ("credit", "billing", "insufficient_quota",
                                   "exceededyourcurrentquota")):
            return _Fail("key", f"Нямаш кредити в {who}. Превключи на "
                                f"Gemini — безплатен е. ({d})")
        return _Fail("busy", f"Твърде много заявки към {model} в минута.")
    if r.status_code == 400:
        if any(w in low for w in ("thinking", "safety", "reasoning",
                                  "max_completion_tokens", "unknown name",
                                  "unrecognized", "invalid json payload",
                                  "response_format", "responsemimetype")):
            return _Fail("extras", d)
        return _Fail("bad", f"Грешка 400 от {who} ({model}): {d}")
    if r.status_code >= 500:
        return _Fail("busy", f"{who} е претоварен ({model}): {d}")
    return _Fail("bad", f"Грешка {r.status_code} от {who}: {d}")


# ---------------------------------------------------------------------------
# Заявки
# ---------------------------------------------------------------------------

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


def _thinking_cfg(model):
    low = model.lower()
    if low.startswith("gemini-2.5-flash"):
        return {"thinkingBudget": 0}
    if low.startswith("gemini-2.5-pro") or low.startswith("gemini-2.0"):
        return None
    # 3.x и псевдонимите: „low" се приема от всички flash модели
    return {"thinkingLevel": "low"}


SAFETY = [{"category": c, "threshold": "BLOCK_ONLY_HIGH"} for c in (
    "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")]


def _gemini(key, model, system, messages, audio, timeout, max_tokens, plain):
    contents = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].append({"text": m["content"]})
        else:
            contents.append({"role": role, "parts": [{"text": m["content"]}]})
    if not contents or contents[0]["role"] != "user":
        contents.insert(0, {"role": "user", "parts": [{"text": "(начало)"}]})
    if audio:
        contents[-1]["parts"].insert(0, {"inline_data": {
            "mime_type": "audio/wav",
            "data": base64.b64encode(audio).decode("ascii")}})
    gen = {"responseMimeType": "application/json",
           "maxOutputTokens": max_tokens}
    body = {"contents": contents,
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": gen}
    if not plain:
        th = _thinking_cfg(model)
        if th:
            gen["thinkingConfig"] = th
        body["safetySettings"] = SAFETY
    r = requests.post(f"{GEMINI_BASE}/models/{model}:generateContent",
                      params={"key": key}, json=body, timeout=timeout)
    bad = _classify(r, model, "Gemini")
    if bad:
        raise bad
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        block = (data.get("promptFeedback") or {}).get("blockReason")
        if block:
            return json.dumps({"say": "Хм, за това по-добре да замълча."})
        raise _Fail("busy", "Моделът не върна отговор.")
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        reason = cands[0].get("finishReason", "")
        if reason == "MAX_TOKENS":
            raise _Fail("extras", "Отговорът не се побра (MAX_TOKENS).")
        if reason in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            return json.dumps({"say": "Хм, за това по-добре да замълча."})
        raise _Fail("busy", f"Празен отговор ({reason or '?'}).")
    return text


def _openai(key, model, system, messages, audio, timeout, max_tokens, plain):
    msgs = [{"role": "system", "content": system}] + messages
    body = {"model": model, "messages": msgs,
            "response_format": {"type": "json_object"}}
    if not plain:
        body["max_completion_tokens"] = max_tokens
        if model.startswith(("gpt-5", "o")):
            body["reasoning_effort"] = "low"
    r = requests.post(f"{OPENAI_BASE}/chat/completions", timeout=timeout,
                      headers={"Authorization": f"Bearer {key}"}, json=body)
    bad = _classify(r, model, "OpenAI")
    if bad:
        raise bad
    return r.json()["choices"][0]["message"]["content"]


def ask(cfg, system, messages, audio=None, timeout=45, force=False,
        max_tokens=4096):
    """Пита модела. Връща (речник с отговор, грешка).

    force=True минава покрай ограничителя — за режисьора и за неща,
    които администраторът поиска изрично.
    """
    provider = cfg.get("ai_provider") or "gemini"
    key = (cfg.get("gemini_key" if provider == "gemini" else "openai_key")
           or "").strip()
    if not key:
        _note_err("Няма API ключ.")
        return None, "Няма API ключ."
    if not force and not limiter.allow():
        STATS["busy"] += 1
        return None, "busy"

    call = _gemini if provider == "gemini" else _openai
    who = "Gemini" if provider == "gemini" else "OpenAI"
    chain = _chain(cfg, provider, key)
    errors = []
    refreshed = False
    i = 0
    while i < len(chain):
        model = chain[i]
        plain = model in _plain
        try:
            text = call(key, model, system, messages, audio, timeout,
                        max_tokens, plain)
            if errors:
                log.info("AI", f"Отговори {model} (преди това: "
                               f"{errors[-1][:80]})")
            _note_ok(model)
            return _parse_json(text), None
        except _Fail as f:
            now = time.time()
            if f.kind == "key":
                _note_err(f.text)
                return None, f.text
            if f.kind == "extras" and not plain:
                _plain.add(model)          # същият модел, без екстри
                continue
            errors.append(f.text)
            if f.kind == "gone":
                _dead[model] = now + LIST_TTL
                log.warn("AI", f.text[:200])
                if not refreshed:
                    # Може би списъкът е остарял — взимаме го наново
                    refreshed = True
                    list_models(provider, key, refresh=True)
                    extra = [m for m in _chain(cfg, provider, key)
                             if m not in chain]
                    chain.extend(extra[:3])
            elif f.kind == "day":
                _dead[model] = now + _seconds_to_quota_reset()
                log.warn("AI", f.text + " Минавам на друг модел.")
            elif f.kind in ("busy", "extras"):
                _cool[model] = now + 45
            else:                           # bad
                _cool[model] = now + 120
        except requests.exceptions.Timeout:
            errors.append(f"{model}: отговорът се забави")
            _cool[model] = time.time() + 45
        except requests.exceptions.RequestException as e:
            # Няма интернет — другите модели също няма да минат
            msg = f"Няма връзка с {who}: {type(e).__name__}"
            _note_err(msg)
            return None, msg
        except Exception as e:
            errors.append(f"{model}: {type(e).__name__}: {e}")
            _cool[model] = time.time() + 60
        i += 1

    if errors and all("Дневният лимит" in e for e in errors):
        msg = ("Дневните безплатни лимити на всички модели свършиха. "
               "Нулират се към 10–11 ч. българско време.")
    elif errors:
        msg = errors[-1]
    else:
        msg = "Няма наличен модел."
    _note_err(msg)
    return None, msg
