"""Where spaceManUI keeps its own files (settings, user skins, the agent's stderr log)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def config_dir() -> Path:
    if env := os.environ.get("SPACEMAN_HOME"):
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "spaceman"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "spaceman"


def state_dir() -> Path:
    if env := os.environ.get("SPACEMAN_HOME"):
        return Path(env) / "state"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "spaceman"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "spaceman"
