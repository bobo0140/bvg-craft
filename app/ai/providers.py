"""
providers.py — връзката с езиковите модели. Няколко безплатни услуги
работят заедно като един „мозък".

Всяка услуга има свой безплатен лимит. Сами те стигат за няколко
разговора; заедно стигат AI-то да мисли постоянно. Затова всяка заявка
минава по верига: първо най-подходящата услуга и модел за задачата,
после следващите. Модел, който е спрян, изчерпан или зает, се помни и
се заобикаля, докато пак стане годен.

Две роли:
  smart — режисьорът, историята, босовете: по-умни, по-бавни модели;
  fast  — репликите на духовете и селяните: бързи модели.

Моделите идват и си отиват (gemini-2.0-flash спря на 1 юни 2026, а 2.5
не се дават на нови ключове), затова не разчитаме на твърд списък:
питаме всяка услуга кои модели има за дадения ключ.
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

# Всички услуги. "kind": "openai" значи OpenAI-съвместим адрес.
PROVIDERS = {
    "gemini": {"name": "Google Gemini", "key": "gemini_key",
               "kind": "gemini", "free": True,
               "url": "https://aistudio.google.com/apikey",
               "note": "Безплатно, без карта. Умен и с голям лимит."},
    "groq": {"name": "Groq", "key": "groq_key", "kind": "openai",
             "base": "https://api.groq.com/openai/v1", "free": True,
             "url": "https://console.groq.com/keys",
             "note": "Безплатно, без карта. Най-бързият — 1000 заявки "
                     "на ден за всеки модел."},
    "mistral": {"name": "Mistral", "key": "mistral_key", "kind": "openai",
                "base": "https://api.mistral.ai/v1", "free": True,
                "url": "https://console.mistral.ai/api-keys",
                "note": "Безплатни кредити всеки месец, без карта."},
    "openrouter": {"name": "OpenRouter", "key": "openrouter_key",
                   "kind": "openai", "base": "https://openrouter.ai/api/v1",
                   "free": True, "url": "https://openrouter.ai/keys",
                   "note": "Безплатни модели (50 заявки на ден) — резерва."},
    "cerebras": {"name": "Cerebras", "key": "cerebras_key",
                 "kind": "openai", "base": "https://api.cerebras.ai/v1",
                 "free": False, "url": "https://cloud.cerebras.ai",
                 "note": "Много бърз; иска карта за пробния кредит."},
    "openai": {"name": "OpenAI", "key": "openai_key", "kind": "openai",
               "base": OPENAI_BASE, "free": False,
               "url": "https://platform.openai.com/api-keys",
               "note": "Платено."},
}

ORDER = {"smart": ["gemini", "groq", "mistral", "openrouter", "cerebras",
                   "openai"],
         "fast": ["groq", "gemini", "mistral", "cerebras", "openrouter",
                  "openai"]}

# Ако списъкът не може да се вземе (няма интернет, временна грешка)
GUESS = {
    "gemini": ["gemini-flash-latest", "gemini-3.8-flash", "gemini-3.7-flash",
               "gemini-3.6-flash", "gemini-3.5-flash",
               "gemini-flash-lite-latest", "gemini-3.5-flash-lite",
               "gemini-3.1-flash-lite", "gemini-2.5-flash"],
    "groq": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b",
             "llama-3.3-70b-versatile"],
    "mistral": ["mistral-large-latest", "mistral-medium-latest",
                "mistral-small-latest"],
    "openrouter": ["openai/gpt-oss-120b:free",
                   "meta-llama/llama-3.3-70b-instruct:free"],
    "cerebras": ["gpt-oss-120b", "llama-3.3-70b"],
    "openai": ["gpt-4.1-mini", "gpt-4o-mini", "gpt-5-mini", "gpt-5-nano"],
}

# Модели, които не са за чат (картинки, глас, вграждане и т.н.)
NOT_CHAT = ("tts", "image", "live", "embedding", "embed", "audio",
            "transcribe", "robotics", "computer-use", "omni", "veo",
            "imagen", "lyria", "aqa", "learnlm", "banana", "native",
            "translate", "research", "antigravity", "customtools", "gemma",
            "realtime", "search", "instruct", "moderation", "whisper",
            "dall-e", "codex", "sora", "babbage", "davinci", "chat-latest",
            "guard", "orpheus", "ocr", "voxtral", "codestral", "devstral",
            "pixtral", "vision", "coder", "safeguard", "playai", "distil",
            "compound", "allam", "tool-use", "-vl", "speech")

CHAIN_LEN = 5            # колко модела от една услуга пробваме
LIST_TTL = 6 * 3600      # колко време важи списъкът с модели


# ---------------------------------------------------------------------------
# Ограничител: защита от спам в чата, не от лимитите на услугите
# ---------------------------------------------------------------------------

class RateLimiter:
    """Не повече от N заявки в минута — отвъд това отказваме веднага."""

    def __init__(self, per_minute=30):
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
         "last_error_at": 0.0, "model": "", "models": [], "provider": "",
         "by": {}, "today": 0, "day": ""}

_lock = threading.Lock()
_catalog = {}        # (услуга, хеш на ключа) -> (кога, [модели], грешка)
_dead = {}           # "услуга:модел" -> до кога не го пробваме
_cool = {}           # "услуга:модел" -> до кога е зает
_plain = set()       # "услуга:модел", които не приемат допълнителни неща
_badkey = {}         # услуга -> (до кога, защо) — ключът е отхвърлен


def _pstat(prov):
    return STATS["by"].setdefault(prov, {"ok": 0, "err": 0, "last_error": "",
                                         "model": "", "last_ok": 0.0})


def _note_ok(model="", prov=""):
    STATS["ok"] += 1
    STATS["last_ok"] = time.time()
    day = time.strftime("%Y-%m-%d")
    if STATS["day"] != day:
        STATS["day"], STATS["today"] = day, 0
    STATS["today"] += 1
    if model:
        STATS["model"] = model
    if prov:
        STATS["provider"] = prov
        ps = _pstat(prov)
        ps["ok"] += 1
        ps["model"] = model
        ps["last_ok"] = time.time()


def _note_err(msg, prov=""):
    STATS["err"] += 1
    STATS["last_error"] = msg
    STATS["last_error_at"] = time.time()
    if prov:
        ps = _pstat(prov)
        ps["err"] += 1
        ps["last_error"] = msg[:200]


def _khash(key):
    return hashlib.sha1((key or "").encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
# Списък с модели и подредба
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


def _size(mid):
    """Колко милиарда параметъра — по името (120b, 70b, 8x7b...)."""
    low = mid.lower()
    m = re.search(r"(\d+)x(\d+(?:\.\d+)?)b", low)
    if m:
        return float(m.group(1)) * float(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?)b\b", low)
    if m:
        return float(m.group(1))
    for word, val in (("large", 120.0), ("medium", 60.0), ("small", 24.0),
                      ("ministral", 8.0), ("instant", 8.0), ("mini", 20.0),
                      ("nano", 5.0)):
        if word in low:
            return val
    return 30.0


def _generic_rank(mid, role):
    s = _size(mid)
    dated = bool(re.search(r"-\d{4}(?:-\d{2})?(?:-\d{2})?$|-\d{4}$", mid))
    latest = mid.endswith("-latest")
    if role == "fast":
        # бързите: 8–40 млрд. са сладката точка
        return (0 if 8 <= s <= 40 else 1, abs(s - 24), dated, not latest,
                mid)
    return (dated and not latest, -s, not latest, mid)


def _rank(prov, models, role):
    if prov == "gemini":
        ranked = sorted(models, key=_gemini_rank)
        if role == "fast":
            # lite-моделите са по-бързи и имат отделен лимит
            lite = [m for m in ranked if "lite" in m]
            ranked = lite + [m for m in ranked if "lite" not in m]
        return ranked
    if prov == "openai":
        return sorted(models, key=_openai_rank)
    return sorted(models, key=lambda m: _generic_rank(m, role))


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


def _headers(prov, key):
    h = {"Authorization": f"Bearer {key}"}
    if prov == "openrouter":
        h["HTTP-Referer"] = "https://bvgworld.online"
        h["X-Title"] = "BVG Craft"
    return h


def _fetch_compatible(prov, key):
    base = PROVIDERS[prov]["base"]
    r = requests.get(f"{base}/models", timeout=20, headers=_headers(prov, key))
    if r.status_code >= 400:
        raise RuntimeError(_api_error(r))
    data = r.json()
    items = data.get("data", data if isinstance(data, list) else [])
    ids = []
    for m in items:
        mid = m.get("id", "") if isinstance(m, dict) else str(m)
        if not mid or not _is_chat(mid):
            continue
        if prov == "openai" and not mid.startswith("gpt-"):
            continue
        if prov == "openrouter" and not mid.endswith(":free"):
            continue
        if isinstance(m, dict) and m.get("active") is False:
            continue
        ids.append(mid)
    return sorted(set(ids))


# стари имена (ползват се от тестовете)
def _fetch_openai(key):
    return sorted(_fetch_compatible("openai", key), key=_openai_rank)


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
        if provider == "gemini":
            models = _fetch_gemini(key)
        else:
            models = _fetch_compatible(provider, key)
        err = None if models else "Услугата не върна нито един модел за чат."
    except Exception as e:
        models, err = [], f"{type(e).__name__}: {e}"
    with _lock:
        if models or not hit:
            _catalog[ck] = (now, models, err)
    if models:
        STATS["models"] = models[:20]
        _pstat(provider)["models"] = len(models)
        log.info("AI", f"{PROVIDERS[provider]['name']}: "
                       f"{', '.join(_rank(provider, models, 'smart')[:5])}" +
                       (" …" if len(models) > 5 else ""))
    elif err:
        _pstat(provider)["last_error"] = err[:200]
        log.warn("AI", f"{PROVIDERS[provider]['name']}: не мога да взема "
                       f"списъка с модели: {err[:160]}")
    return models, err


def _key(cfg, prov):
    return (cfg.get(PROVIDERS[prov]["key"]) or "").strip()


def configured(cfg):
    """Услугите, за които има ключ, по реда на важност."""
    return [p for p in ORDER["smart"] if _key(cfg, p)]


def _order(cfg, role):
    order = list(ORDER.get(role, ORDER["smart"]))
    first = (cfg.get("ai_provider") or "auto").strip()
    if first in PROVIDERS and first in order:
        order.remove(first)
        order.insert(0, first)
    now = time.time()
    keyed = [p for p in order if _key(cfg, p)]
    good = [p for p in keyed if _badkey.get(p, (0, ""))[0] <= now]
    return good or keyed


def _chain(cfg, prov, key, role="smart"):
    models, _ = list_models(prov, key)
    base = _rank(prov, models, role) if models else list(GUESS.get(prov, []))
    want = ""
    if prov == "gemini":
        want = (cfg.get("gemini_model") or "auto").strip()
    elif prov == "openai":
        want = (cfg.get("openai_model") or "auto").strip()
    if want and want != "auto" and (not models or want in models):
        base = [want] + [m for m in base if m != want]
    now = time.time()
    alive = [m for m in base if _dead.get(f"{prov}:{m}", 0) <= now]
    fresh = [m for m in alive if _cool.get(f"{prov}:{m}", 0) <= now]
    busy = [m for m in alive if _cool.get(f"{prov}:{m}", 0) > now]
    chain = (fresh + busy)[:CHAIN_LEN]
    return chain


def model_status(cfg):
    """За интерфейса: всяка услуга — има ли ключ, модели, кой отговаря."""
    now = time.time()
    provs = []
    for p, info in PROVIDERS.items():
        key = _key(cfg, p)
        with _lock:
            hit = _catalog.get((p, _khash(key))) if key else None
        ps = STATS["by"].get(p, {})
        bad = _badkey.get(p, (0, ""))
        provs.append({
            "key": p, "name": info["name"], "free": info["free"],
            "url": info["url"], "note": info["note"],
            "set": bool(key), "models": len(hit[1]) if hit else 0,
            "top": _rank(p, hit[1], "smart")[:3] if hit and hit[1] else [],
            "model": ps.get("model", ""), "ok": ps.get("ok", 0),
            "err": ps.get("err", 0),
            "last_error": bad[1] if bad[0] > now else ps.get("last_error", ""),
            "bad_key": bad[0] > now,
            "list_error": hit[2] if hit else None})
    gem = next(x for x in provs if x["key"] == "gemini")
    with _lock:
        ghit = _catalog.get(("gemini", _khash(_key(cfg, "gemini"))))
    return {"provider": STATS.get("provider") or "",
            "providers": provs,
            "models": (ghit[1] if ghit else [])[:25],
            "working": STATS.get("model") or "",
            "chosen": cfg.get("gemini_model") or "auto",
            "unavailable": sorted(m for m, t in _dead.items() if t > now),
            "busy": sorted(m for m, t in _cool.items() if t > now),
            "list_error": gem["list_error"], "today": STATS.get("today", 0)}


def forget_models():
    """След смяна на ключ — наново."""
    with _lock:
        _catalog.clear()
    _dead.clear()
    _cool.clear()
    _plain.clear()
    _badkey.clear()
    STATS["model"] = ""


# ---------------------------------------------------------------------------
# Грешки
# ---------------------------------------------------------------------------

def _api_error(r) -> str:
    try:
        data = r.json()
        err = data.get("error") if isinstance(data, dict) else None
        if err is None and isinstance(data, dict):
            err = data.get("message") or data.get("detail") or ""
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
            "invalid_api_key" in flat or "invalidapikey" in flat or \
            "unauthorized" in flat:
        return _Fail("key", f"Ключът не е приет от {who}: {d}")
    if r.status_code == 404 or any(w in low for w in (
            "not found", "is not supported", "no longer available",
            "not available", "has been deprecated", "does not exist",
            "do not have access", "does not have access", "decommissioned",
            "model_not_found", "no endpoints found")):
        return _Fail("gone", f"Моделът {model} не е достъпен: {d}")
    if r.status_code == 403:
        if "model" in low:
            return _Fail("gone", f"Моделът {model} не е разрешен: {d}")
        return _Fail("key", f"{who} отказа достъп: {d}")
    if r.status_code == 402:
        return _Fail("key", f"{who}: няма кредити ({d})")
    if r.status_code == 413:
        return _Fail("busy", f"Заявката е твърде голяма за {model}.")
    if r.status_code == 429:
        if "limit:0" in flat or "limit: 0" in low:
            return _Fail("gone", f"{model} няма безплатен лимит: {d}")
        if any(w in flat for w in ("perday", "per_day", "daily", "rpd",
                                   "requestsperday", "tpd", "tokensperday")):
            return _Fail("day", f"Дневният лимит на {model} ({who}) свърши.")
        if any(w in flat for w in ("credit", "billing", "insufficient_quota",
                                   "exceededyourcurrentquota")):
            return _Fail("key", f"Нямаш кредити в {who}. ({d})")
        return _Fail("busy", f"Твърде много заявки към {model} в минута.")
    if r.status_code == 400:
        if any(w in low for w in ("thinking", "safety", "reasoning",
                                  "max_completion_tokens", "unknown name",
                                  "unrecognized", "invalid json payload",
                                  "response_format", "responsemimetype",
                                  "json mode", "json_object",
                                  "not supported with", "unsupported",
                                  "extra inputs", "property")):
            return _Fail("extras", d)
        if "context" in low and "length" in low or "too long" in low or \
                "maximum" in low and "token" in low:
            return _Fail("busy", f"Заявката е твърде дълга за {model}.")
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
    # някои модели пишат мислите си в <think>...</think>
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
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
    return {"thinkingLevel": "low"}


SAFETY = [{"category": c, "threshold": "BLOCK_ONLY_HIGH"} for c in (
    "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")]


def _gemini(prov, key, model, system, messages, audio, timeout, max_tokens,
            plain):
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


def _compatible(prov, key, model, system, messages, audio, timeout,
                max_tokens, plain):
    msgs = [{"role": "system", "content": system}] + messages
    body = {"model": model, "messages": msgs}
    if not plain:
        body["response_format"] = {"type": "json_object"}
        if prov == "openai":
            body["max_completion_tokens"] = max_tokens
        else:
            body["max_tokens"] = max_tokens
        low = model.lower()
        if "gpt-oss" in low or (prov == "openai" and
                                model.startswith(("gpt-5", "o"))):
            body["reasoning_effort"] = "low"
    else:
        body["max_tokens"] = max_tokens
    r = requests.post(f"{PROVIDERS[prov]['base']}/chat/completions",
                      timeout=timeout, headers=_headers(prov, key), json=body)
    who = PROVIDERS[prov]["name"]
    bad = _classify(r, model, who)
    if bad:
        raise bad
    data = r.json()
    try:
        choice = data["choices"][0]
        text = (choice.get("message") or {}).get("content") or ""
    except (KeyError, IndexError, TypeError):
        raise _Fail("busy", f"{who}: странен отговор.")
    if not text.strip():
        if choice.get("finish_reason") == "length":
            raise _Fail("extras", "Отговорът не се побра.")
        raise _Fail("busy", f"{who}: празен отговор.")
    return text


def ask(cfg, system, messages, audio=None, timeout=45, force=False,
        max_tokens=4096, role="smart"):
    """Пита „мозъка". Връща (речник с отговор, грешка).

    Минава по всички услуги с ключ, по реда за ролята, и по моделите във
    всяка. force=True минава покрай ограничителя срещу спам в чата.
    """
    order = _order(cfg, role)
    if not order:
        _note_err("Няма API ключ.")
        return None, "Няма API ключ."
    if not force and not limiter.allow():
        STATS["busy"] += 1
        return None, "busy"

    errors = []
    tried = 0
    for prov in order:
        key = _key(cfg, prov)
        call = _gemini if PROVIDERS[prov]["kind"] == "gemini" else _compatible
        who = PROVIDERS[prov]["name"]
        chain = _chain(cfg, prov, key, role)
        refreshed = False
        i = 0
        while i < len(chain) and tried < 12:
            model = chain[i]
            mk = f"{prov}:{model}"
            plain = mk in _plain
            tried += 1
            try:
                text = call(prov, key, model, system, messages, audio,
                            timeout, max_tokens, plain)
                if errors:
                    log.info("AI", f"Отговори {who} / {model} (преди това: "
                                   f"{errors[-1][:80]})")
                _note_ok(model, prov)
                return _parse_json(text), None
            except _Fail as f:
                now = time.time()
                if f.kind == "key":
                    _badkey[prov] = (now + 600, f.text[:200])
                    _note_err(f.text, prov)
                    errors.append(f.text)
                    break                       # следващата услуга
                if f.kind == "extras" and not plain:
                    _plain.add(mk)              # същият модел, без екстри
                    tried -= 1
                    continue
                errors.append(f.text)
                _pstat(prov)["last_error"] = f.text[:200]
                if f.kind == "gone":
                    _dead[mk] = now + LIST_TTL
                    log.warn("AI", f.text[:200])
                    if not refreshed:
                        refreshed = True
                        list_models(prov, key, refresh=True)
                        extra = [m for m in _chain(cfg, prov, key, role)
                                 if m not in chain]
                        chain.extend(extra[:3])
                elif f.kind == "day":
                    _dead[mk] = now + _seconds_to_quota_reset()
                    log.warn("AI", f.text + " Минавам нататък.")
                elif f.kind in ("busy", "extras"):
                    _cool[mk] = now + 45
                else:                           # bad
                    _cool[mk] = now + 120
            except requests.exceptions.Timeout:
                errors.append(f"{who}/{model}: отговорът се забави")
                _cool[mk] = time.time() + 45
            except requests.exceptions.RequestException as e:
                # тази услуга не се достига — пробваме следващата
                errors.append(f"Няма връзка с {who}: {type(e).__name__}")
                _cool[mk] = time.time() + 60
                break
            except Exception as e:
                errors.append(f"{who}/{model}: {type(e).__name__}: {e}")
                _cool[mk] = time.time() + 60
            i += 1

    if errors and all("Дневният лимит" in e for e in errors):
        msg = ("Дневните безплатни лимити свършиха. Нулират се към 10–11 ч. "
               "българско време. Добави още един безплатен ключ (Groq, "
               "Mistral), за да не спира.")
    elif errors:
        msg = errors[-1]
    else:
        msg = "Няма наличен модел."
    _note_err(msg)
    return None, msg
