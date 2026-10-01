"""Настройките. Всяка нова настройка се добавя само тук."""
import json
import os
import secrets
import threading

from . import paths

DEFAULTS = {
    # сървър
    "mc_version": "",              # празно = най-новата стабилна
    "ram_gb": 4,
    "gc_profile": "g1",            # g1 (препоръка на Paper) или zgc
    "difficulty": "normal",
    "gamemode": "survival",
    "max_players": 20,
    "view_distance": 10,
    "simulation_distance": 8,
    "motd": "BVG WORLD",
    "port": 25565,
    "offline_mode": True,
    "open_to_network": False,
    "pvp": True,
    "hardcore": False,
    "spawn_protection": 16,
    "rcon_port": 25575,
    "rcon_password": "",

    # играчи
    "whitelist_on": True,
    "whitelist": [],               # имена
    "admins": [],                  # кой може да дава големи задачи на AI-то

    # AI
    "ai_provider": "gemini",
    "gemini_key": "",
    "gemini_model": "gemini-2.5-flash",
    "openai_key": "",
    "openai_model": "gpt-4o-mini",
    "ai_enabled": True,
    "chaos": 2,                    # 0 тихо, 1 леко, 2 весело, 3 хаос
    "director_minutes": 8,         # на колко минути режисьорът мисли за събитие
    "roast_deaths": True,
    "greet_joins": True,
    "characters": {},              # включени/изключени и позиции на героите
}

SECRETS = ("gemini_key", "openai_key", "rcon_password")


class Config:
    def __init__(self):
        self._lock = threading.Lock()
        self.data = dict(DEFAULTS)
        self.load()
        if not self.data.get("rcon_password"):
            self.data["rcon_password"] = secrets.token_urlsafe(14)
            self.save()

    def load(self):
        try:
            with open(paths.CONFIG, encoding="utf-8") as f:
                saved = json.load(f)
            for k, v in saved.items():
                self.data[k] = v
        except (OSError, json.JSONDecodeError):
            pass

    def save(self):
        with self._lock:
            tmp = paths.CONFIG + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, paths.CONFIG)

    def get(self, key, default=None):
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key, value):
        self.data[key] = value
        self.save()

    def update(self, values: dict):
        """Приема само познати ключове. Празни тайни не трият запазените."""
        for k, v in values.items():
            if k not in DEFAULTS:
                continue
            if k in SECRETS and (v is None or v == "" or str(v).startswith("•")):
                continue
            self.data[k] = v
        self.save()

    def public(self) -> dict:
        """За интерфейса — ключовете не се връщат в чист вид."""
        out = dict(self.data)
        for k in SECRETS:
            v = out.get(k) or ""
            out[k + "_set"] = bool(v)
            out[k] = ("•" * 8 + v[-4:]) if v else ""
        return out
