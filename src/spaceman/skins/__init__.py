"""Skins: the look and words of the interface, loaded from TOML.

The built-in skin (navigator.toml) is complete. A user skin only needs the keys it changes; everything
else falls back to the built-in. User skins live in <config dir>/skins/<name>.toml.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from ..eye import eyeart
from ..paths import config_dir

BUILTIN = "navigator"


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _rgb(value: str) -> tuple:
    return eyeart.rgb(value)


@dataclass
class Skin:
    name: str
    title: str
    subtitle: str
    window_title: str
    text: dict
    status: dict
    eye: dict
    tools: dict
    colors: dict              # hex strings, for CSS
    palettes: dict = field(default_factory=dict)   # name -> {edge, mid, deep, light} as rgb tuples
    buttons: list = field(default_factory=list)    # [Button]: clickable readouts that run a command
    title_indent: int = 5                           # cells between the eye and the title/subtitle lines

    def rgb(self, key: str) -> tuple:
        return _rgb(self.colors[key])

    def palette(self, mode: str) -> dict:
        name = self.eye.get(mode, self.eye.get("idle", "pink"))
        return self.palettes.get(name) or eyeart.PALETTES.get(name) or eyeart.PALETTES["pink"]

    def status_word(self, mode: str) -> str:
        return self.status.get(mode, mode.upper())

    def tool_mode(self, kind: str | None) -> str:
        return self.tools.get(kind or "other", self.tools.get("other", "think"))

    def box_colors(self) -> dict:
        return {k: self.rgb(k) for k in ("background", "box_fill", "box_outline", "rim_dim", "rim_bright", "box_inner")}


@dataclass
class Button:
    """A skin button: a clickable readout on the MODEL line, after MODE and THINK. Picking a choice runs `run` with {choice} filled in
    (shell-quoted) in the agent's working directory. `status` (optional) is a command whose first output line's last
    word is shown as the current value. restart_agent restarts the agent afterwards (the conversation is kept), for
    settings the agent only reads when it starts."""
    label: str
    choices: list
    run: str
    status: str = ""
    restart_agent: bool = False
    description: str = ""


def _buttons(raw) -> list:
    out = []
    for b in raw or []:
        try:
            choices = [str(c) for c in b["choices"]]
            if b["label"] and b["run"] and choices:
                out.append(Button(label=str(b["label"]), choices=choices, run=str(b["run"]),
                                  status=str(b.get("status", "")), restart_agent=bool(b.get("restart_agent", False)),
                                  description=str(b.get("description", ""))))
        except (KeyError, TypeError):
            print(f"spaceman: skipping a skin button without label, choices and run: {b!r}", file=sys.stderr)
    return out


def _builtin_data() -> dict:
    return tomllib.loads(resources.files(__package__).joinpath(f"{BUILTIN}.toml").read_text(encoding="utf-8"))


def user_skin_dir() -> Path:
    return config_dir() / "skins"


def available() -> list[str]:
    names = {BUILTIN}
    d = user_skin_dir()
    if d.is_dir():
        names |= {p.stem for p in d.glob("*.toml")}
    return sorted(names)


def load(name: str | None) -> Skin:
    """Load a skin by name (or a path to a .toml file). Unknown names fall back to the built-in."""
    data = _builtin_data()
    if name and name != BUILTIN:
        path = Path(name) if name.endswith(".toml") else user_skin_dir() / f"{name}.toml"
        try:
            data = _merge(data, tomllib.loads(path.read_text(encoding="utf-8")))
        except (OSError, tomllib.TOMLDecodeError) as e:
            print(f"spaceman: skin {name!r} not loaded ({e}); using {BUILTIN}", file=sys.stderr)
    palettes = {}
    for pname, pal in (data.get("palettes") or {}).items():
        try:
            palettes[pname] = {k: _rgb(pal[k]) for k in ("edge", "mid", "deep", "light")}
        except (KeyError, ValueError):
            pass
    return Skin(
        name=data.get("name", "Skin"), title=data.get("title", ""), subtitle=data.get("subtitle", ""),
        window_title=data.get("window_title", "spaceManUI"), text=data["text"], status=data["status"],
        eye=data["eye"], tools=data["tools"], colors=data["colors"], palettes=palettes,
        buttons=_buttons(data.get("buttons")),
        title_indent=max(1, int((data.get("layout") or {}).get("title_indent", 5))),
    )
