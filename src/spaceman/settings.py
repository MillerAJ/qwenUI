"""Choices that stick between runs: <config dir>/settings.json."""
from __future__ import annotations

import json

from .paths import config_dir

DEFAULTS = {"skin": "navigator", "sounds": False, "motion": True, "thoughts": True}


def path():
    return config_dir() / "settings.json"


def load() -> dict:
    s = dict(DEFAULTS)
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
        for k, v in DEFAULTS.items():
            if type(data.get(k)) is type(v):
                s[k] = data[k]
    except (OSError, ValueError):
        pass
    return s


def save(settings: dict) -> None:
    try:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except OSError:
        pass
