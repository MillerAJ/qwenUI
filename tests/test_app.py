"""End-to-end tests against the fake agent: the real app, the real ACP client, headless."""
import asyncio
import os
import sys
from pathlib import Path

import pytest

from spaceman.app import SpacemanApp
from spaceman.eye.widget import ChoiceMenu, EyeConsole
from spaceman.messages import AgentBubble, PermissionPrompt, QuestionPrompt, ThoughtBlock, ToolCard, UserBubble

FAKE = [sys.executable, str(Path(__file__).with_name("fake_agent.py"))]
SETTINGS = {"skin": "navigator", "sounds": False, "motion": True, "thoughts": True}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACEMAN_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def make_app(tmp_path, **kw):
    return SpacemanApp(FAKE, tmp_path, settings=dict(SETTINGS), save_settings=False, **kw)


async def until(pilot, cond, timeout=10.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("timed out waiting")
        await pilot.pause(0.05)


async def type_and_send(pilot, app, text):
    app.typing.value = text
    await pilot.press("enter")


async def test_connects_and_fills_readouts(isolated):
    app = make_app(isolated)
    async with app.run_test(size=(120, 46)) as pilot:
        await until(pilot, lambda: app.session_id == "fake-session-1")
        await until(pilot, lambda: app.eye_console.eye.mode == "idle")
        hud = app.eye_console.hud
        assert hud["model"] == "Fake Model 1"
        assert hud["mode"] == "default"
        assert hud["think"] == "MEDIUM"
        assert hud["status"] == "ONLINE"
        assert app.eye_console.size.height >= 8


@pytest.mark.parametrize("key,expected", [("y", "proceed_once"), ("n", "cancel"), ("a", "proceed_always_project")])
async def test_turn_with_permission(isolated, key, expected):
    app = make_app(isolated)
    async with app.run_test(size=(120, 46)) as pilot:
        await until(pilot, lambda: app.session_id is not None and app.eye_console.eye.mode == "idle")
        await type_and_send(pilot, app, "make a file")
        await until(pilot, lambda: len(app.query(PermissionPrompt)) == 1 and app.prompts)
        assert app.eye_console.eye.mode == "wait"
        assert app.focused is app.query_one(PermissionPrompt)
        await pilot.press(key)
        await until(pilot, lambda: not app.busy)
        assert (isolated / "permission-answer.txt").read_text() == expected
        card = app.query_one(ToolCard)
        assert card.call["status"] == ("failed" if expected == "cancel" else "completed")
        assert app.query(UserBubble)
        assert app.query(ThoughtBlock).first().collapsed
        bubble = app.query(AgentBubble).last()
        assert "Done." in bubble.text
        assert len(app.query(".chart")) == 1, "chart block rendered as a chart"
        assert "%" in app.eye_console.hud["ctx"]
        assert app.eye_console.eye.mode == "idle"


async def test_click_permission_button(isolated):
    app = make_app(isolated)
    async with app.run_test(size=(120, 46)) as pilot:
        await until(pilot, lambda: app.session_id is not None and app.eye_console.eye.mode == "idle")
        await type_and_send(pilot, app, "make a file")
        await until(pilot, lambda: app.prompts)
        await pilot.click("#opt-2")                       # "Allow"
        await until(pilot, lambda: not app.busy)
        assert (isolated / "permission-answer.txt").read_text() == "proceed_once"


async def test_escape_cancels(isolated):
    app = make_app(isolated)
    async with app.run_test(size=(120, 46)) as pilot:
        await until(pilot, lambda: app.session_id is not None and app.eye_console.eye.mode == "idle")
        await type_and_send(pilot, app, "slow please")
        await until(pilot, lambda: app.bubble is not None and "word3" in app.bubble.text)
        await pilot.press("escape")
        await until(pilot, lambda: not app.busy)
        assert "Stopped." in " ".join(str(n.render()) for n in app.query(".notice"))


async def test_readout_menus_and_cycle(isolated):
    app = make_app(isolated)
    async with app.run_test(size=(120, 46)) as pilot:
        await until(pilot, lambda: app.session_id is not None and app.eye_console.eye.mode == "idle")
        await pilot.pause(0.2)
        console = app.query_one(EyeConsole)
        assert "think" in console.hits and "mode" in console.hits
        row, a, _ = console.hits["think"]
        await pilot.click(EyeConsole, offset=(a, row))
        await until(pilot, lambda: len(app.screen.query(ChoiceMenu)) == 1)
        await pilot.press("down", "enter")                # medium -> xhigh
        await until(pilot, lambda: app.eye_console.hud["think"] == "XHIGH")
        await pilot.press("f4")                           # default -> auto-edit
        await until(pilot, lambda: app.eye_console.hud["mode"] == "auto-edit")


async def test_resume_replays_history(isolated):
    app = make_app(isolated, continue_last=True)
    async with app.run_test(size=(120, 46)) as pilot:
        await until(pilot, lambda: app.session_id == "old-session-9")
        await until(pilot, lambda: app.eye_console.eye.mode == "idle")
        assert app.query_one(UserBubble).text == "hello from before"
        assert app.query(AgentBubble).last().text == "old answer"


async def test_missing_agent_reports_error(isolated):
    app = SpacemanApp([sys.executable, "-c", "import sys; sys.stderr.write('auth required'); sys.exit(3)"],
                      isolated, settings=dict(SETTINGS), save_settings=False)
    async with app.run_test(size=(120, 46)) as pilot:
        await until(pilot, lambda: len(app.query(".notice.error")) >= 1)
        text = " ".join(str(n.render()) for n in app.query(".notice.error"))
        assert "qwen" in text and "sign" in text


@pytest.mark.parametrize("size", [(70, 30), (90, 26), (160, 50)])
async def test_layout_sizes(isolated, size):
    app = make_app(isolated)
    async with app.run_test(size=size) as pilot:
        await until(pilot, lambda: app.session_id is not None)
        await pilot.pause(0.3)
        console = app.eye_console
        assert console.region.bottom <= size[1] - 1       # above the footer
        typing = app.typing
        assert console.region.contains(typing.region.x, typing.region.y)
        assert app.log_view.size.height >= 4


BUTTON_SKIN = """
name = "Buttons"
title = "TEST"
subtitle = "SUB"
[layout]
title_indent = 2
[[buttons]]
label = "EGM - 🐘🐡"
choices = ["full", "partial", "off"]
status = '{py} btn.py status'
run = '{py} btn.py set {{choice}}'
restart_agent = true
"""

BUTTON_SCRIPT = """import sys
if sys.argv[1] == "set":
    open("mode.txt", "w").write(sys.argv[2])
    print("set", sys.argv[2])
else:
    print("MODE: " + open("mode.txt").read())
"""


def button_app(tmp_path):
    skins_dir = tmp_path / "home" / "skins"
    skins_dir.mkdir(parents=True)
    py = '"' + sys.executable + '"'
    (skins_dir / "buttons.toml").write_text(BUTTON_SKIN.format(py=py), encoding="utf-8")
    (tmp_path / "btn.py").write_text(BUTTON_SCRIPT, encoding="utf-8")
    (tmp_path / "mode.txt").write_text("off", encoding="utf-8")
    return SpacemanApp(FAKE, tmp_path, settings=dict(SETTINGS, skin="buttons"), save_settings=False)


async def test_skin_button_runs_and_restarts_agent(isolated):
    app = button_app(isolated)
    async with app.run_test(size=(120, 46)) as pilot:
        await until(pilot, lambda: app.session_id is not None and app.eye_console.eye.mode == "idle")
        await until(pilot, lambda: app.eye_console.button_values == ["off"])
        await pilot.pause(0.2)
        console = app.query_one(EyeConsole)
        assert "button:0" in console.hits
        first_pid = app.client.proc.pid
        sid = app.session_id
        row, a, _ = console.hits["button:0"]
        await pilot.click(EyeConsole, offset=(a, row))
        await until(pilot, lambda: len(app.screen.query(ChoiceMenu)) == 1)
        await pilot.press("home", "enter")                 # off -> full (first choice)
        await until(pilot, lambda: (isolated / "mode.txt").read_text() == "full")
        await until(pilot, lambda: app.client and app.client.proc.pid != first_pid and app.eye_console.eye.mode == "idle")
        await until(pilot, lambda: app.eye_console.button_values == ["full"])
        assert app.session_id == sid                       # same conversation after the restart
        assert app.eye_console.eye.mode != "error"


async def test_title_indent_moves_title_only(isolated):
    app = button_app(isolated)
    async with app.run_test(size=(120, 46)) as pilot:
        await until(pilot, lambda: app.session_id is not None and app.eye_console.eye.mode == "idle")
        await pilot.pause(0.2)
        console = app.query_one(EyeConsole)
        lines = console.render().plain.splitlines()
        title_row = next(i for i, l in enumerate(lines) if "TEST" in l)
        model_row = next(i for i, l in enumerate(lines) if "MODEL" in l)
        assert lines[model_row].index("MODEL") - lines[title_row].index("TEST") == 3


async def test_questions_are_shown_and_answered(isolated):
    import json
    app = make_app(isolated)
    async with app.run_test(size=(120, 60)) as pilot:
        await until(pilot, lambda: app.session_id is not None and app.eye_console.eye.mode == "idle")
        await type_and_send(pilot, app, "ask me")
        await until(pilot, lambda: app.prompts and app.query("#q-submit"))
        q = app.query_one(QuestionPrompt)
        assert not app.query(PermissionPrompt)
        assert len(q.query("RadioButton")) == 2 and len(q.query("Checkbox")) == 3
        await pilot.click("#q0-o1")                       # Cyan
        await pilot.click("#q1-o0")                       # Sounds
        await pilot.click("#q1-o2")                       # Images
        q.query_one("#q1-other").value = "Haptics"
        await pilot.click("#q-submit")
        await until(pilot, lambda: not app.busy)
        got = json.loads((isolated / "question-answer.txt").read_text())
        assert got == {"optionId": "proceed_once", "answers": {"0": "Cyan", "1": "Sounds, Images, Haptics"}}


async def test_questions_cancel(isolated):
    import json
    app = make_app(isolated)
    async with app.run_test(size=(120, 60)) as pilot:
        await until(pilot, lambda: app.session_id is not None and app.eye_console.eye.mode == "idle")
        await type_and_send(pilot, app, "ask me")
        await until(pilot, lambda: app.prompts and app.query("#q-submit"))
        await pilot.click("#q-cancel")
        await until(pilot, lambda: not app.busy)
        got = json.loads((isolated / "question-answer.txt").read_text())
        assert got == {"optionId": None, "answers": None}
