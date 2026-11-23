"""The `spaceman` command.

  spaceman [--skin NAME] [-c | -r ID] [--mode MODE] [--effort LEVEL] [--cwd DIR] [--agent "CMD"]
  spaceman ext install|remove display|navigator     opt-in Qwen Code extensions shipped with spaceManUI
  spaceman look apply|remove|status                 make the stock `qwen` terminal match the Navigator skin
  spaceman skins                                    list skins (and where your own go)
  spaceman paths                                    where spaceManUI keeps its files
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from importlib import resources
from pathlib import Path

from . import skins
from .acp import default_command, resolve_command
from .paths import config_dir, state_dir

EXTENSIONS = {"display": "spaceman-display", "navigator": "spaceman-navigator"}


def extras_path(*parts: str) -> Path:
    return Path(str(resources.files("spaceman").joinpath("extras", *parts)))


def qwen_home() -> Path:
    return Path(os.environ["QWEN_HOME"]) if os.environ.get("QWEN_HOME") else Path.home() / ".qwen"


# --- ext ---------------------------------------------------------------------------------------------
def cmd_ext(args: list[str]) -> int:
    if len(args) < 2 or args[0] not in ("install", "remove") or args[1] not in EXTENSIONS:
        print("usage: spaceman ext install|remove display|navigator [qwen options, e.g. --scope project]\n\n"
              "  display    teaches the agent to draw charts and show images in spaceManUI (recommended)\n"
              "  navigator  the calm ship's-computer voice that goes with the Navigator skin")
        return 2
    action, which, extra = args[0], args[1], args[2:]
    qwen = resolve_command(["qwen"])[0]
    if action == "install":
        cmd = [qwen, "extensions", "install", str(extras_path(which)), "--consent", *extra]
    else:
        cmd = [qwen, "extensions", "uninstall", EXTENSIONS[which], *extra]
    print("running:", " ".join(shlex.quote(c) for c in cmd))
    try:
        return subprocess.call(cmd)
    except OSError as e:
        print(f"spaceman: could not run qwen: {e}", file=sys.stderr)
        return 1


# --- look ---------------------------------------------------------------------------------------------
def cmd_look(args: list[str]) -> int:
    settings_path = qwen_home() / "settings.json"
    saved = qwen_home() / "spaceman-look-previous-ui.json"
    action = args[0] if args else "status"
    try:
        current = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    except ValueError as e:
        print(f"spaceman: {settings_path} is not valid JSON ({e}); not touching it.", file=sys.stderr)
        return 1
    ui = current.get("ui") or {}
    if action == "status":
        print("look:", "navigator" if ui.get("theme") == "Navigator" else "your own / default", f"({settings_path})")
        return 0
    if action not in ("apply", "remove"):
        print("usage: spaceman look apply|remove|status")
        return 2
    if settings_path.exists():
        backup = settings_path.with_name(f"settings.json.bak-spaceman-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(settings_path, backup)
        print("backup:", backup)
    if action == "apply":
        if ui.get("theme") != "Navigator":
            saved.write_text(json.dumps({"ui": ui} if "ui" in current else {}, indent=2), encoding="utf-8")
        look = json.loads(extras_path("look", "navigator.json").read_text(encoding="utf-8"))
        current["ui"] = {**ui, **look}
    else:
        if not saved.exists():
            print("Nothing to undo: the Navigator look was not applied by spaceman.")
            return 0
        previous = json.loads(saved.read_text(encoding="utf-8"))
        if "ui" in previous:
            current["ui"] = previous["ui"]
        else:
            current.pop("ui", None)
        saved.unlink()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, settings_path)
    print(f"look {'applied' if action == 'apply' else 'removed'}. Restart `qwen` to see it.")
    return 0


# --- info -------------------------------------------------------------------------------------------------
def cmd_skins() -> int:
    for name in skins.available():
        print(name)
    print(f"\nYour own skins go in {skins.user_skin_dir()}  (copy the built-in one as a start:"
          f"\n  {resources.files('spaceman.skins').joinpath('navigator.toml')})")
    return 0


def cmd_paths() -> int:
    print("settings:   ", config_dir() / "settings.json")
    print("skins:      ", skins.user_skin_dir())
    print("agent log:  ", state_dir() / "agent-stderr.log")
    print("qwen home:  ", qwen_home())
    return 0


# --- main ------------------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "ext":
        return cmd_ext(argv[1:])
    if argv and argv[0] == "look":
        return cmd_look(argv[1:])
    if argv and argv[0] == "skins":
        return cmd_skins()
    if argv and argv[0] == "paths":
        return cmd_paths()

    p = argparse.ArgumentParser(prog="spaceman", description="A skinnable, animated terminal UI for Qwen Code.",
                                epilog="Also: spaceman ext | look | skins | paths  (see the README)")
    p.add_argument("--skin", help="skin to use (default: the one chosen in Settings; built-in: navigator)")
    p.add_argument("-c", "--continue", dest="cont", action="store_true", help="pick up the most recent conversation")
    p.add_argument("-r", "--resume", metavar="ID", help="pick up a saved conversation by its id")
    p.add_argument("--mode", help="approval mode for this session: plan, default, auto-edit, auto, yolo")
    p.add_argument("--effort", help="reasoning effort for this session, e.g. none, low, medium, high, xhigh")
    p.add_argument("--cwd", default=os.getcwd(), help="working directory for the agent (default: here)")
    p.add_argument("--agent", help='agent command line (default: "qwen --acp"; env SPACEMAN_AGENT)')
    p.add_argument("--version", action="version", version="spaceman-ui 0.1.0")
    a = p.parse_args(argv)

    command = shlex.split(a.agent, posix=sys.platform != "win32") if a.agent else default_command()
    if not shutil.which(command[0]) and not Path(command[0]).exists():
        print(f"spaceman: can't find `{command[0]}`. Install Qwen Code first (npm install -g @qwen-code/qwen-code), "
              "run `qwen` once to sign in, then try again.", file=sys.stderr)
        return 1
    cwd = Path(a.cwd).expanduser().resolve()
    if not cwd.is_dir():
        print(f"spaceman: {cwd} is not a folder", file=sys.stderr)
        return 1
    if "COLORTERM" not in os.environ:
        os.environ["COLORTERM"] = "truecolor"            # the eye is drawn in true colour

    from .app import SpacemanApp                          # import late: textual-image probes the terminal
    SpacemanApp(command, cwd, skin_name=a.skin, resume=a.resume, continue_last=a.cont,
                start_mode=a.mode, start_effort=a.effort).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
