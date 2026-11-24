"""spaceManUI: the app. A conversation log on top, the eye console at the bottom, Qwen Code behind it (ACP)."""
from __future__ import annotations

import asyncio
import os
import shlex
import time
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Footer, Header, OptionList, Select, Static
from textual.widgets.option_list import Option

from . import settings as settings_mod
from . import skins
from .acp import AcpClient, AcpError
from .eye.widget import ChoiceMenu, EyeConsole, TypingArea
from .messages import AgentBubble, Notice, PermissionPrompt, QuestionPrompt, ThoughtBlock, ToolCard, UserBubble
from .paths import state_dir
from .sound import Sound

TYPING_ROWS = 4
TOOL_HOLD = 2.0          # seconds a tool's eye colour shows at least
CONFIG_KEYS = {"think": "reasoning_effort", "mode": "mode"}
PLAN_ICON = {"pending": "○", "in_progress": "◐", "completed": "●"}


def build_css(skin) -> str:
    c = skin.colors
    return f"""
    Screen {{ background: {c['background']}; }}
    Header {{ background: {c['header']}; }}
    #log {{ background: {c['background']}; padding: 1 2 0 2; }}
    .you {{ background: {c['user_bubble']}; color: #ffffff; border-left: thick {c['user_accent']}; padding: 0 1; margin: 1 0 0 4; }}
    .bot {{ background: {c['agent_bubble']}; color: #ffffff; border-left: thick {c['agent_accent']}; padding: 0 1; margin: 1 4 0 0; }}
    .thought {{ background: {c['background']}; color: {c['thought']}; margin: 1 4 0 0; padding: 0; border: none; }}
    .thought CollapsibleTitle {{ color: {c['thought']}; text-style: italic; padding: 0; }}
    .thought Contents {{ padding: 0 0 0 2; }}
    .thought-text {{ color: {c['thought']}; text-style: italic; }}
    .tool {{ color: {c['tool']}; margin: 1 4 0 1; }}
    .plan {{ color: {c['hud_value']}; margin: 1 4 0 1; }}
    .notice {{ color: {c['hud_dim']}; margin: 1 0 0 1; }}
    .notice.error {{ color: {c['tool_fail']}; }}
    .chart {{ height: 16; margin: 1 4 0 1; }}
    .picture {{ height: 16; width: auto; margin: 1 4 0 1; }}
    .permission {{ height: auto; background: {c['menu_bg']}; border: round {c['wait_border']};
                   padding: 0 1; margin: 1 4 0 1; }}
    .permission:focus {{ border: round {c['rim_bright']}; }}
    .permission-buttons {{ height: auto; margin-top: 1; }}
    .permission-buttons Button {{ margin-right: 1; }}
    #typing {{ margin: 0; padding: 0; border: none; height: 1; background: {c['box_fill']}; color: {c['typing']};
              scrollbar-size: 0 0; }}
    #typing:focus {{ border: none; background: {c['box_fill']}; }}
    #typing > .text-area--placeholder {{ color: {c['placeholder']}; }}
    #console {{ width: 100%; margin: 0 1 0 0; background: {c['background']}; }}
    ChoiceMenu {{ background: {c['menu_bg']}; border: round {c['menu_border']}; }}
    ChoiceMenu > .option-list--option-highlighted {{ background: {c['user_bubble']}; }}
    ModalScreen {{ align: center middle; }}
    .dialog {{ width: 72; max-height: 80%; height: auto; padding: 1 2; background: {c['menu_bg']}; border: heavy {c['menu_border']}; }}
    .dialog-title {{ text-style: bold; color: {c['hud_title']}; padding-bottom: 1; }}
    .dialog-hint {{ color: {c['hud_dim']}; padding-top: 1; }}
    .dialog OptionList {{ height: auto; max-height: 20; }}
    .dialog Button {{ margin-top: 1; }}
    """


class SessionsScreen(ModalScreen):
    """Pick a saved conversation to pick up (shared with the `qwen` terminal and its web shell)."""
    BINDINGS = [("escape", "dismiss(None)", "Close")]

    def __init__(self, sessions: list[dict]) -> None:
        super().__init__()
        self.sessions = sessions

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("Saved conversations", classes="dialog-title")
            if self.sessions:
                yield OptionList(*[Option(self._label(s), id=s["sessionId"]) for s in self.sessions])
            else:
                yield Static("None yet in this folder.")
            yield Static("Enter opens one. Esc closes.", classes="dialog-hint")

    @staticmethod
    def _label(s: dict) -> str:
        when = (s.get("updatedAt") or "")[:16].replace("T", " ")
        title = " ".join((s.get("title") or s["sessionId"]).split())
        return f"{when}  {title[:52]}"

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)


class SettingsScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Close"), ("f2", "close", "Close")]

    def __init__(self, settings: dict) -> None:
        super().__init__()
        self.settings = settings

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static("Settings", classes="dialog-title")
            yield Static("Skin (takes effect next start)")
            names = skins.available()
            yield Select([(n, n) for n in names], value=self.settings["skin"] if self.settings["skin"] in names else skins.BUILTIN,
                         allow_blank=False, id="skin")
            yield Checkbox("Text sounds: a soft blip as letters appear", self.settings["sounds"], id="sounds")
            yield Checkbox("Animation: eye movement and the pulsing rim", self.settings["motion"], id="motion")
            yield Checkbox("Show the model's thinking", self.settings["thoughts"], id="thoughts")
            yield Static("Click the eye to make it blink. Three quick clicks: a surprise.\n"
                         "MODE and THINK in the readouts are clickable (F4 / F3 cycle them).", classes="dialog-hint")
            yield Button("Done", id="done", variant="success")

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self.app.apply_setting(event.checkbox.id, event.value)

    def on_select_changed(self, event: Select.Changed) -> None:
        if isinstance(event.value, str):
            self.app.apply_setting("skin", event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()

    def action_close(self) -> None:
        self.dismiss()


class SpacemanApp(App):
    BINDINGS = [
        Binding("escape", "stop", "Stop reply"),
        Binding("f2", "settings", "Settings"),
        Binding("f3", "cycle('think')", "Thinking"),
        Binding("f4", "cycle('mode')", "Mode"),
        Binding("ctrl+r", "sessions", "Sessions"),
        Binding("ctrl+n", "new_session", "New"),
    ]

    def __init__(self, command: list[str], cwd: Path, skin_name: str | None = None, resume: str | None = None,
                 continue_last: bool = False, start_mode: str | None = None, start_effort: str | None = None,
                 settings: dict | None = None, save_settings: bool = True) -> None:
        self.settings = settings or settings_mod.load()
        self.save_settings = save_settings
        self.skin = skins.load(skin_name or self.settings["skin"])
        self.CSS = build_css(self.skin)
        super().__init__()
        self.title = self.skin.window_title
        self.command, self.cwd = command, Path(cwd)
        self.resume, self.continue_last = resume, continue_last
        self.start_mode, self.start_effort = start_mode, start_effort
        self.client: AcpClient | None = None
        self.session_id: str | None = None
        self.config: dict = {}           # config option id -> option (mode, model, reasoning_effort, ...)
        self.commands: list = []         # slash commands the agent offers
        self.busy = False
        self.replaying = False
        self.sound = Sound()
        self.bubble: AgentBubble | None = None
        self.thought: ThoughtBlock | None = None
        self.plan_card: Static | None = None
        self.tools: dict[str, ToolCard] = {}
        self.prompts: list[PermissionPrompt] = []
        self.last_user: UserBubble | None = None
        self.msgs = 0
        self._cursor, self._tick = "▁", 0
        self._turn_started = 0.0
        self._last_kind = ""
        self.closing = False
        self.restarting = False
        self._after_connect = ""          # a note to show once the (re)connect finishes
        self._restart_resume = False      # the next connect is a button restart (resume, else start fresh)

    # -- layout --------------------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="log")
        yield EyeConsole(self.skin, id="console")
        yield Footer()

    @property
    def log_view(self) -> VerticalScroll:
        return self.query_one("#log", VerticalScroll)

    @property
    def eye_console(self) -> EyeConsole:
        return self.query_one(EyeConsole)

    @property
    def typing(self) -> TypingArea:
        return self.query_one(TypingArea)

    def on_mount(self) -> None:
        self.apply_settings()
        self.set_interval(1 / 12, self.pulse)
        self.typing.focus()
        self.connect()

    def fit_eye(self) -> None:
        h, w = self.size.height, self.size.width
        n = max(12, min(26, (h * 45 // 100) // 2 * 2, (w // 5) // 2 * 2))
        console = self.eye_console
        if n != console.eye.n:
            console.eye.n = n
        box_h = max(n // 2, n // 4 + TYPING_ROWS + 2)
        if console.styles.height is None or console.styles.height.value != box_h:
            console.styles.height = box_h
        console.place_input()

    def pulse(self) -> None:
        if self.closing:
            return
        try:
            self._pulse()
        except NoMatches:
            pass                                          # the screen is going away (quitting)

    def _pulse(self) -> None:
        self._tick += 1
        self.fit_eye()
        self.eye_console.talking = self.bubble is not None
        if self._tick % 6 == 0:
            self._cursor = " " if self._cursor == "▁" else "▁"
        if self.bubble:
            self.bubble.flush(self._cursor if self.busy and not self.replaying else "")
        if self.thought:
            self.thought.flush()
        log = self.log_view
        if self.busy and log.max_scroll_y - log.scroll_y < 6:
            log.scroll_end(animate=False)

    # -- settings ---------------------------------------------------------------------------------
    def apply_setting(self, name: str, value) -> None:
        self.settings[name] = value
        if self.save_settings:
            settings_mod.save(self.settings)
        if name == "skin":
            self.note(f"Skin set to {value}. It takes effect the next time you start spaceman.")
        self.apply_settings()

    def apply_settings(self) -> None:
        self.eye_console.eye.motion = self.settings["motion"]
        self.sound.on = self.settings["sounds"]

    def action_settings(self) -> None:
        if isinstance(self.screen, SettingsScreen):
            self.pop_screen()
        else:
            self.push_screen(SettingsScreen(self.settings))

    def get_system_commands(self, screen):
        yield from super().get_system_commands(screen)
        yield SystemCommand("Settings", "Skin, sounds, animation, thinking (F2)", self.action_settings)
        yield SystemCommand("Sessions", "Pick up a saved conversation (Ctrl+R)", self.action_sessions)
        yield SystemCommand("New conversation", "Start fresh (Ctrl+N)", self.action_new_session)
        for i, b in enumerate(self.skin.buttons):
            for choice in b.choices:
                yield SystemCommand(f"{b.label}: {choice}", b.description or f"Set {b.label} to {choice}",
                                    lambda i=i, choice=choice: self.run_button(i, choice))

    # -- the eye and readouts ------------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        self.eye_console.eye.mode = mode
        self.eye_console.set_hud(status=self.skin.status_word(mode))

    def tool_mode_start(self, mode: str) -> None:
        """A tool started: show its state, and hold a working colour (search, fetch...) at least TOOL_HOLD seconds,
        so a quick tool is still a visible pulse rather than a flicker."""
        self.set_mode(mode)
        if mode not in ("think", "speak", "idle"):
            self.eye_console.eye.flash(mode, TOOL_HOLD)

    def update_hud(self) -> None:
        model = self.config.get("model")
        effort = self.config.get("reasoning_effort")
        mode = self.config.get("mode")
        self.eye_console.set_hud(
            model=_option_name(model) if model else (self.client.agent_info.get("title", "...") if self.client else "..."),
            mode=(mode or {}).get("currentValue", "") if mode else "",
            think=_option_name(effort, short=True) if effort else "",
        )

    def on_eye_console_readout_clicked(self, event: EyeConsole.ReadoutClicked) -> None:
        if event.name.startswith("button:"):
            for menu in self.screen.query(ChoiceMenu):
                menu.close(None)
                return
            i = int(event.name.split(":", 1)[1])
            b = self.skin.buttons[i]
            current = self.eye_console.button_values[i]
            self.screen.mount(ChoiceMenu(event.name, [(c, c) for c in b.choices], current, event.x, event.y))
            return
        opt = self.config.get(CONFIG_KEYS.get(event.name, ""))
        if not opt:
            return
        for menu in self.screen.query(ChoiceMenu):
            menu.close(None)
            return
        choices = [(o["value"], o.get("name") or o["value"]) for o in opt.get("options") or []]
        self.screen.mount(ChoiceMenu(event.name, choices, opt.get("currentValue", ""), event.x, event.y))

    def on_choice_menu_chosen(self, event: ChoiceMenu.Chosen) -> None:
        if event.value is not None and event.key.startswith("button:"):
            self.run_button(int(event.key.split(":", 1)[1]), event.value)
        elif event.value is not None:
            self.set_config(CONFIG_KEYS[event.key], event.value)
        self.call_later(self.typing.focus)

    def action_cycle(self, which: str) -> None:
        opt = self.config.get(CONFIG_KEYS[which])
        if not opt or not opt.get("options"):
            return
        values = [o["value"] for o in opt["options"]]
        cur = opt.get("currentValue")
        nxt = values[(values.index(cur) + 1) % len(values)] if cur in values else values[0]
        self.set_config(CONFIG_KEYS[which], nxt)

    @work(group="config")
    async def set_config(self, config_id: str, value: str) -> None:
        if not (self.client and self.session_id):
            return
        try:
            result = await self.client.set_config(self.session_id, config_id, value)
        except AcpError as e:
            self.note(f"Could not change {config_id}: {e.message}", "error")
            return
        self.take_config(result.get("configOptions"))

    def take_config(self, options: list | None) -> None:
        if options:
            self.config = {o["id"]: o for o in options if "id" in o}
            self.update_hud()

    # -- skin buttons ------------------------------------------------------------------------------
    async def _shell(self, command: str, timeout: float = 120) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_shell(command, cwd=str(self.cwd), stdin=asyncio.subprocess.DEVNULL,
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return -1, f"timed out after {timeout:.0f}s"
        return proc.returncode, out.decode(errors="replace").strip()

    @work(group="buttons")
    async def refresh_buttons(self) -> None:
        for i, b in enumerate(self.skin.buttons):
            if not b.status:
                continue
            code, out = await self._shell(b.status, timeout=30)
            first = out.splitlines()[0].split() if code == 0 and out else []
            self.eye_console.set_button_value(i, first[-1] if first else "?")

    @work(exclusive=True, group="button-run")
    async def run_button(self, index: int, choice: str) -> None:
        b = self.skin.buttons[index]
        if b.restart_agent and self.busy:
            self.note(f"Still working on a reply. Esc stops it first, then set {b.label} again.")
            return
        code, out = await self._shell(b.run.replace("{choice}", shlex.quote(choice)))
        if code != 0:
            self.note(f"{b.label} {choice} failed (exit {code}):\n{out[-1500:]}", "error")
            self.refresh_buttons()
            return
        self.eye_console.set_button_value(index, choice)
        self.refresh_buttons()
        message = out[-600:] or f"{b.label}: {choice}"
        if b.restart_agent and self.client and self.client.alive:
            self._after_connect = message + "\nAgent restarted with it."
            await self.restart_agent()
        else:
            self.note(message)

    async def restart_agent(self) -> None:
        """Stop the agent and start it again on the same conversation (for settings it reads at startup)."""
        sid = self.session_id
        self.commands = []                # the new agent sends its own list
        self.restarting = True
        try:
            if self.client:
                await self.client.close()
        finally:
            self.restarting = False
        self.resume, self.continue_last = sid, False
        self._restart_resume = True
        self.connect()

    # -- connecting --------------------------------------------------------------------------------
    @work(exclusive=True, group="connect")
    async def connect(self) -> None:
        self.set_mode("offline")
        self.client = AcpClient(self.command, self.cwd, on_update=self.on_update, on_permission=self.on_permission,
                                on_exit=self.on_agent_exit, on_notice=self.on_notice,
                                stderr_log=state_dir() / "agent-stderr.log")
        try:
            await self.client.start()
        except (OSError, AcpError) as e:
            await self.client.settle()
            self.agent_failed(f"Could not start the agent ({' '.join(self.command)}): {e}", auth=True)
            return
        info = self.client.agent_info
        self.sub_title = f"{info.get('title') or info.get('name') or 'agent'} {info.get('version', '')}  ·  {self.cwd}"
        target = self.resume
        if not target and self.continue_last:
            try:
                sessions = await self.client.list_sessions()
                target = sessions[0]["sessionId"] if sessions else None
            except AcpError:
                target = None
        restarting, self._restart_resume = self._restart_resume, False
        try:
            if target:
                try:
                    await self.open_session(target)
                except AcpError:
                    if not restarting:
                        raise
                    # after a button restart: a conversation with no messages yet was never saved, so start fresh
                    self.replaying = False
                    await self.start_session()
            else:
                await self.start_session()
        except AcpError as e:
            self.agent_failed(f"The agent would not start a session: {e.message}", auth=True)
            return
        if self.start_mode:
            await self._set_now("mode", self.start_mode)
        if self.start_effort:
            await self._set_now("reasoning_effort", self.start_effort)
        self.set_mode("idle")
        self.refresh_buttons()
        if self._after_connect:
            self.note(self._after_connect)
            self._after_connect = ""

    async def _set_now(self, config_id: str, value: str) -> None:
        try:
            self.take_config((await self.client.set_config(self.session_id, config_id, value)).get("configOptions"))
        except AcpError as e:
            self.note(f"Could not set {config_id} to {value}: {e.message}", "error")

    async def start_session(self) -> None:
        result = await self.client.new_session()
        self.session_id = result["sessionId"]
        self.take_session(result)
        greeting = self.skin.text.get("greeting")
        if greeting:
            self.note(greeting)
        self.maybe_tip()

    def maybe_tip(self) -> None:
        """Once, for new users: the display extension is what lets the agent draw charts and show pictures here."""
        if self.settings.get("tip_display_shown"):
            return
        qwen_home = Path(os.environ.get("QWEN_HOME") or Path.home() / ".qwen")
        if (qwen_home / "extensions" / "spaceman-display").exists():
            return
        self.note("Tip: run `spaceman ext install display` once so the agent knows it can draw charts and show "
                  "pictures here. (`spaceman ext install navigator` adds the ship's-computer voice.)")
        self.settings["tip_display_shown"] = True
        if self.save_settings:
            settings_mod.save(self.settings)

    async def open_session(self, session_id: str) -> None:
        self.clear_log()
        self.session_id = session_id
        self.replaying = True
        self.set_mode("fetch")
        try:
            result = await self.client.load_session(session_id)
        finally:
            self.end_message()
            self.replaying = False
        self.take_session(result)
        self.note(f"Picked up conversation {session_id[:8]}.")
        self.log_view.scroll_end(animate=False)
        self.set_mode("idle")

    def take_session(self, result: dict) -> None:
        self.take_config(result.get("configOptions"))
        if "model" not in self.config and result.get("models"):
            m = result["models"]
            names = {x["modelId"]: x.get("name") for x in m.get("availableModels") or []}
            self.config["model"] = {"currentValue": m.get("currentModelId"),
                                    "options": [{"value": k, "name": v} for k, v in names.items()]}
        self.update_hud()

    def agent_failed(self, text: str, auth: bool = False) -> None:
        tail = self.client.stderr_tail() if self.client else ""
        hint = ("\nCheck that Qwen Code works on its own: run `qwen` once in a terminal (sign in if it asks), "
                "then start spaceman again." if auth or "auth" in tail.lower() else "")
        self.note(text + hint + (f"\n\nAgent said:\n{tail[-1500:]}" if tail else ""), "error")
        self.set_mode("error")
        self.eye_console.eye.flash("error", 2)
        self.set_timer(2.2, lambda: self.set_mode("offline"))

    def on_agent_exit(self, code, tail: str) -> None:
        if self.closing or self.restarting:
            return
        self.busy = False
        for p in self.prompts:
            p.cancel()
        self.end_message()
        self.agent_failed(f"The agent stopped (exit code {code}). Ctrl+N starts it again.")
        self.session_id = None

    def on_notice(self, method: str, params: dict) -> None:
        if method.endswith("mode-update") and params.get("currentModeId") and "mode" in self.config:
            self.config["mode"]["currentValue"] = params["currentModeId"]
            self.update_hud()

    # -- sessions --------------------------------------------------------------------------------------
    @work(exclusive=True, group="connect")
    async def action_sessions(self) -> None:
        if not (self.client and self.client.alive):
            return
        if self.busy:
            self.note("Still working on a reply. Esc stops it first.")
            return
        try:
            sessions = await self.client.list_sessions()
        except AcpError as e:
            self.note(f"Could not list conversations: {e.message}", "error")
            return
        chosen = await self.push_screen_wait(SessionsScreen(sessions))
        if chosen:
            try:
                await self.open_session(chosen)
            except AcpError as e:
                self.note(f"Could not open that conversation: {e.message}", "error")

    @work(exclusive=True, group="connect")
    async def action_new_session(self) -> None:
        if self.busy:
            self.note("Still working on a reply. Esc stops it first.")
            return
        if not (self.client and self.client.alive):
            self.connect()
            return
        self.clear_log()
        try:
            await self.start_session()
        except AcpError as e:
            self.note(f"Could not start a new conversation: {e.message}", "error")

    def clear_log(self) -> None:
        self.end_message()
        self.tools.clear()
        self.log_view.remove_children()
        self.last_user = None
        self.msgs = 0

    # -- sending -----------------------------------------------------------------------------------------
    async def on_typing_area_submitted(self, event: TypingArea.Submitted) -> None:
        text = event.text.strip()
        if not text:
            return
        if not (self.client and self.client.alive and self.session_id):
            self.note("No agent connected yet.", "error")
            return
        if self.busy:
            self.note("Still working on the last one. Esc stops it.")
            return
        event.area.value = ""
        await self.log_view.mount(UserBubble(text))
        self.msgs += 1
        self.eye_console.eye.blink()
        self.log_view.scroll_end(animate=False)
        self.run_turn(text)

    @work(exclusive=True, group="turn")
    async def run_turn(self, text: str) -> None:
        self.busy = True
        self._turn_started = time.monotonic()
        self.set_mode("think")
        stop = None
        try:
            result = await self.client.prompt(self.session_id, text)
            stop = result.get("stopReason")
        except AcpError as e:
            self.note(f"Turn failed: {e.message}", "error")
            self.eye_console.eye.flash("error", 1.5)
        finally:
            self.busy = False
            if not self.closing:
                for p in list(self.prompts):
                    p.cancel()
                self.end_message()
                self.set_mode("idle")
            self.plan_card = None
        if self.closing:
            return
        if stop == "cancelled":
            self.note("Stopped.")
        elif stop in ("max_tokens", "max_turn_requests", "refusal"):
            self.note(f"The turn ended early: {stop}.")

    async def action_stop(self) -> None:
        if self.busy and self.client and self.session_id:
            for p in self.prompts:
                p.cancel()
            await self.client.cancel(self.session_id)
            self.set_mode("idle")

    # -- what the agent sends -----------------------------------------------------------------------------
    async def on_update(self, session_id: str, u: dict) -> None:
        if self.closing or (session_id and session_id != self.session_id):
            return
        kind = u.get("sessionUpdate")
        try:
            await self._handle_update(kind, u)
        finally:
            if kind in ("user_message_chunk", "agent_message_chunk", "agent_thought_chunk", "tool_call"):
                self._last_kind = kind

    async def _handle_update(self, kind: str, u: dict) -> None:
        log = self.log_view
        if kind == "user_message_chunk":                      # only seen while replaying a saved conversation
            text = (u.get("content") or {}).get("text", "")
            if self._last_kind == kind and self.last_user is not None:
                self.last_user.add(text)                      # the same message, in more than one piece
            else:
                self.end_message()
                self.last_user = UserBubble(text)
                await log.mount(self.last_user)
                self.msgs += 1
        elif kind == "agent_thought_chunk":
            text = (u.get("content") or {}).get("text", "")
            if not text or not self.settings["thoughts"]:
                return
            if self.bubble:
                self.end_bubble()
            if self.thought is None:
                self.thought = ThoughtBlock(self.skin.text.get("thinking", "thinking"))
                await log.mount(self.thought)
            self.thought.add(text)
            if not self.replaying:
                self.set_mode("think")
        elif kind == "agent_message_chunk":
            text = (u.get("content") or {}).get("text", "")
            if not text:
                return
            self.end_thought()
            if self.bubble is None:
                if not text.strip():
                    return                                    # don't open a bubble for leading blank lines
                self.bubble = AgentBubble()
                await log.mount(self.bubble)
            self.bubble.add(text)
            if not self.replaying:
                self.set_mode("speak")
                self.sound.blip(self)
        elif kind == "tool_call":
            self.end_message()
            card = ToolCard(u, self.skin.colors)
            self.tools[u.get("toolCallId", "")] = card
            await log.mount(card)
            if not self.replaying:
                self.tool_mode_start(self.skin.tool_mode(u.get("kind"), u.get("title", "")))
        elif kind == "tool_call_update":
            tid = u.get("toolCallId", "")
            card = self.tools.get(tid)
            if card is None:
                if not u.get("title"):
                    return
                self.end_message()
                card = ToolCard(u, self.skin.colors)
                self.tools[tid] = card
                await log.mount(card)
            else:
                card.merge(u)
            status = u.get("status")
            if status in ("completed", "failed"):
                for img in card.images():
                    await log.mount(img, after=card)
                if not self.replaying:
                    if status == "failed":
                        self.eye_console.eye.flash("error", 0.8)
                    self.set_mode("think")
            elif status == "in_progress" and not self.replaying:
                self.tool_mode_start(self.skin.tool_mode(card.call.get("kind"), card.call.get("title", "")))
        elif kind == "plan":
            await self.show_plan(u.get("entries") or [])
        elif kind == "usage_update":
            used, size = u.get("used"), u.get("size")
            if used is not None and size:
                self.eye_console.set_hud(ctx=f"{used * 100 / size:.0f}%  ({_k(used)}/{_k(size)})")
        elif kind == "current_mode_update":
            if "mode" in self.config:
                self.config["mode"]["currentValue"] = u.get("currentModeId")
                self.update_hud()
        elif kind == "config_option_update":
            self.take_config(u.get("configOptions"))
        elif kind == "available_commands_update":
            self.commands = u.get("availableCommands") or []
        elif kind == "session_info_update":
            if u.get("title"):
                self.sub_title = str(u["title"])[:80]

    async def show_plan(self, entries: list) -> None:
        out = Text("Plan\n", style="bold")
        for e in entries:
            out.append(f"  {PLAN_ICON.get(e.get('status'), '○')} {e.get('content', '')}\n",
                       style="dim" if e.get("status") == "completed" else "")
        out.rstrip()
        if self.plan_card is None or not self.plan_card.is_attached:
            self.end_message()
            self.plan_card = Static(out, classes="plan")
            await self.log_view.mount(self.plan_card)
        else:
            self.plan_card.update(out)

    async def on_permission(self, session_id: str, params: dict) -> str | None:
        self.end_message()
        prompt = QuestionPrompt(params) if QuestionPrompt.wants(params) else PermissionPrompt(params)
        self.prompts.append(prompt)
        await self.log_view.mount(prompt)
        self.log_view.scroll_end(animate=False)
        self.set_mode("wait")
        self.eye_console.eye.blink(double=True)
        try:
            return await prompt.future
        finally:
            self.prompts.remove(prompt)
            if not self.closing:
                if self.busy:
                    self.set_mode("think")
                self.call_later(self.typing.focus)

    # -- closing off a message -----------------------------------------------------------------------------
    def end_thought(self) -> None:
        if self.thought:
            self.thought.flush()
            self.thought.collapsed = True
            self.thought = None

    def end_bubble(self) -> None:
        if self.bubble:
            bubble, self.bubble = self.bubble, None
            extras = bubble.finish(self.cwd)
            if extras:
                self.log_view.mount(*extras, after=bubble)

    def end_message(self) -> None:
        self.end_thought()
        self.end_bubble()

    def note(self, text: str, kind: str = "info") -> None:
        try:
            self.log_view.mount(Notice(text, kind))
            self.log_view.scroll_end(animate=False)
        except NoMatches:
            pass                                          # shutting down: nowhere to say it

    async def action_quit(self) -> None:
        self.exit()

    async def on_unmount(self) -> None:
        self.closing = True
        self.workers.cancel_all()
        for p in list(self.prompts):
            if not p.future.done():
                p.future.set_result(None)
        if self.client:
            await self.client.close()


def _option_name(opt: dict, short: bool = False) -> str:
    cur = opt.get("currentValue", "")
    for o in opt.get("options") or []:
        if o.get("value") == cur:
            return cur.upper() if short else (o.get("name") or cur)
    return str(cur).upper() if short else str(cur)


def _k(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{n / 1000:.0f}k"
    return str(n)
