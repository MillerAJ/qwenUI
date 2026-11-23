"""The living eye and the console it sits on.

  EyeState     -> the eye's animation (eyeart.py draws the pixels); its colours come from the skin
  TypingArea   -> where you type, inside the console
  EyeConsole   -> the console: one piece of pixel art (box, metal mount, eye) with readouts above it
  ChoiceMenu   -> a small dropdown that floats over the chat (THINK and MODE readouts open one)
"""
from __future__ import annotations

import math
import random
import time

from rich.cells import cell_len
from rich.color import Color
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import OptionList, TextArea
from textual.widgets.option_list import Option

from . import eyeart

_style_cache: dict = {}


def style(fg: tuple, bg: tuple, bold: bool = False, underline: bool = False) -> Style:
    key = (fg, bg, bold, underline)
    if key not in _style_cache:
        _style_cache[key] = Style(color=Color.from_rgb(*fg), bgcolor=Color.from_rgb(*bg), bold=bold,
                                  underline=underline)
    return _style_cache[key]


class EyeState:
    """The eye, without the drawing. At rest it breathes (the pupil widens and narrows), blinks now and then,
    snaps into focus, now and then does a slow "focus spin" (the pupil narrows while the iris's crescent
    turns once), and sometimes shifts colour for a few seconds. Working colours come from the skin
    (searching, reading, writing, an error...). step() returns a frame."""

    SHIFT_COLOURS = ("cyan", "green", "amber", "ice")
    FOCUS_SECONDS = 2.2

    def __init__(self, skin, n: int = 20) -> None:
        self.skin = skin
        self.n = n
        self.motion = True
        self.mode = "idle"
        self._pal = dict(skin.palette("idle"))
        self._pupil = 0.27
        self._last = self._t0 = time.monotonic()
        self._blinks: list = []
        self._flash = ("idle", 0.0)
        self._rainbow_until = 0.0
        self._clicks: list = []
        now = self._t0
        self._next_blink = now + random.uniform(3, 7)
        self._next_focus = now + random.uniform(9, 18)
        self._next_spin = now + random.uniform(15, 30)
        self._next_shift = now + random.uniform(30, 55)
        self._shift = ("idle", 0.0)
        self._focus_until = 0.0
        self._focus_start = -100.0

    def blink(self, double: bool = False) -> None:
        now = time.monotonic()
        self._blinks = [now] + ([now + 0.36] if double else [])

    def flash(self, mode: str, seconds: float) -> None:
        self._flash = (mode, time.monotonic() + seconds)

    def click(self) -> None:
        """Clicked: blink. Three quick clicks: the rainbow."""
        now = time.monotonic()
        self._clicks = [c for c in self._clicks if now - c < 1.5] + [now]
        self.blink()
        if len(self._clicks) >= 3:
            self._clicks = []
            self._rainbow_until = now + 3.0

    def current_mode(self) -> str:
        return self._flash[0] if time.monotonic() < self._flash[1] else self.mode

    def step(self) -> list:
        now = time.monotonic()
        dt = min(0.25, now - self._last)
        self._last = now
        t = now - self._t0
        mode = self.current_mode()

        if self.motion and mode == "idle":              # the random little life events
            if now >= self._next_blink:
                self.blink(double=random.random() < 0.2)
                self._next_blink = now + random.uniform(5, 10)
            if now >= self._next_focus:
                self._focus_until = now + 0.8
                self._next_focus = now + random.uniform(10, 20)
            if now >= self._next_spin:
                self._focus_start = now
                self._next_spin = now + random.uniform(25, 45)
            if now >= self._next_shift:
                self._shift = (random.choice(self.SHIFT_COLOURS), now + random.uniform(3, 5))
                self._next_shift = now + random.uniform(35, 60)
        fp = (now - self._focus_start) / self.FOCUS_SECONDS          # the focus spin, eased in and out
        spinning = mode == "idle" and 0 <= fp < 1
        ease = fp * fp * (3 - 2 * fp) if spinning else 0.0
        spin = 2 * math.pi * ease

        if now < self._rainbow_until:                   # colour
            self._pal = eyeart.hue_palette(t * 0.4)
        else:
            if mode == "idle" and self.motion and now < self._shift[1]:
                name = self._shift[0]
                target_pal = self.skin.palettes.get(name) or eyeart.PALETTES[name]
            else:
                target_pal = self.skin.palette(mode)
            self._pal = eyeart.mix_palette(self._pal, target_pal, min(1.0, dt * 5))

        if mode == "think":                             # pupil
            target = 0.22 + 0.10 * math.sin(t * 3.9)
        elif mode in ("search", "fetch", "edit", "execute"):
            target = 0.18 + 0.02 * math.sin(t * 8)
        elif mode == "speak":
            target = 0.25 + 0.04 * math.sin(t * 6)
        elif mode == "wait":
            target = 0.30 + 0.05 * math.sin(t * 2.4)
        elif mode == "error":
            target = 0.20 + 0.06 * math.sin(t * 23)
        elif mode == "offline":
            target = 0.33
        elif spinning:
            target = 0.27 - 0.10 * math.sin(math.pi * fp)
        elif now < self._focus_until:
            target = 0.15
        else:
            target = 0.27 + (0.03 * math.sin(t * 1.3) if self.motion else 0)
        self._pupil += (target - self._pupil) * min(1.0, dt * 8)

        self._blinks = [b for b in self._blinks if now - b < 0.24]      # eyelid
        lid = max((1 - abs(2 * ((now - b) / 0.24) - 1) for b in self._blinks), default=0.0)
        if mode == "offline":
            lid = max(lid, 0.55)                        # half shut while there's no agent

        scan = None
        if mode in ("search", "execute"):               # a scanner beam sweeping down the eye
            scan = (now * self.n * 0.9) % (self.n * 1.3) - self.n * 0.15
        return eyeart.eye_pixels(self.n, self._pupil, lid, self._pal, scan, spin)


class TypingArea(TextArea):
    """Your typing area inside the console. It wraps and grows downward as you type. Enter sends;
    Shift+Enter (or Alt+Enter / Ctrl+J) starts a new line. Ctrl+A selects everything, Ctrl+Z / Ctrl+Y undo
    and redo, Ctrl+C copies (with nothing selected here it copies what you highlighted in the conversation)."""

    BINDINGS = [Binding("ctrl+a", "select_all", "Select all", show=False)]
    NEWLINE_KEYS = ("shift+enter", "alt+enter", "ctrl+j")

    class Submitted(Message):
        def __init__(self, area: "TypingArea", text: str) -> None:
            super().__init__()
            self.area, self.text = area, text

    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, v: str) -> None:
        self.load_text(v)
        self.move_cursor(self.document.end)

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self, self.text))
            return
        if event.key in self.NEWLINE_KEYS:
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)

    def action_copy(self) -> None:
        if self.selected_text:
            super().action_copy()
        else:
            self.screen.action_copy_text()

    def rows_needed(self) -> int:
        width = max(1, self.size.width or 1)
        rows = 0
        for line in (self.text + " ").split("\n"):
            rows += max(1, -(-cell_len(line) // width))
        return rows

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if isinstance(self.parent, EyeConsole):
            self.parent.place_input()


class EyeConsole(Widget):
    """The console, drawn as one piece of pixel art (box, metal mount, the eye on its corner), with the
    readouts above it to the right of the eye. You type inside it. Click the eye to make it blink; click the
    underlined readouts (MODE, THINK and any skin buttons) for a menu."""

    DEFAULT_CSS = "EyeConsole { height: 15; overflow: hidden hidden; }"

    class ReadoutClicked(Message):
        """An underlined readout was clicked: name is its key ("mode", "think"); x, y are screen coordinates."""
        def __init__(self, name: str, x: int, y: int) -> None:
            super().__init__()
            self.name, self.x, self.y = name, x, y

    def __init__(self, skin, **kwargs) -> None:
        super().__init__(**kwargs)
        self.skin = skin
        self.eye = EyeState(skin)
        self.talking = False
        self.glow = 0.6
        self._phase = 0.0
        self._last = time.monotonic()
        self._eye_rows = None
        self.typing_rows = 1
        self.hud = {"status": skin.status_word("offline"), "model": "...", "ctx": "", "mode": "", "think": ""}
        self.button_values = ["" for _ in skin.buttons]   # current value shown on each skin button
        self.hits: dict = {}        # readout name -> (row, first col, last col) as last drawn

    def set_button_value(self, index: int, value: str) -> None:
        if 0 <= index < len(self.button_values):
            self.button_values[index] = value
            self.refresh()

    def set_hud(self, **values) -> None:
        self.hud.update({k: v for k, v in values.items() if v is not None})
        self.refresh()

    def hud_lines(self) -> list:
        """The readouts: one list of (text, style, clickable name or None) per line."""
        s, bg = self.skin, self.skin.rgb("background")
        lab, val = style(s.rgb("hud_label"), bg), style(s.rgb("hud_value"), bg)
        link = style(s.rgb("hud_value"), bg, underline=True)
        h = self.hud
        line3 = [("MODEL ", lab, None), (h["model"], val, None)]
        if h["mode"]:
            line3 += [("   MODE ", lab, None), (h["mode"] + " ▾", link, "mode")]
        if h["think"]:
            line3 += [("   THINK ", lab, None), (h["think"] + " ▾", link, "think")]
        line2 = [(s.subtitle, style(s.rgb("hud_dim"), bg), None)]
        if h["ctx"]:
            line2 += [("   CTX ", lab, None), (h["ctx"], val, None)]
        for i, b in enumerate(s.buttons):                  # skin buttons: "LABEL value ▾", click for a menu
            line2 += [(f"   {b.label} ", lab, None), ((self.button_values[i] or "…") + " ▾", link, f"button:{i}")]
        return [
            [(s.title, style(s.rgb("hud_title"), bg, bold=True), None),
             (f"   ▸ {h['status']}", style(s.rgb("hud_status"), bg, bold=True), None)],
            line2,
            line3,
        ]

    def compose(self) -> ComposeResult:
        yield TypingArea(id="typing", soft_wrap=True, show_line_numbers=False, compact=True,
                         highlight_cursor_line=False, placeholder=self.skin.text["placeholder"])

    def on_mount(self) -> None:
        self.set_interval(1 / 12, self._tick)

    def on_resize(self, event) -> None:
        self.place_input()

    def place_input(self) -> None:
        tx0, ty0, tx1, ty1 = eyeart.box_geometry(self.size.width, self.size.height, self.eye.n)["text"]
        area = self.query_one(TypingArea)
        area.styles.offset = (tx0 + 2, ty0)
        area.styles.width = max(1, tx1 - tx0 - 1)
        rows = max(1, min(area.rows_needed(), ty1 - ty0 + 1))
        if rows != self.typing_rows or area.size.height != rows:
            self.typing_rows = rows
            area.styles.height = rows

    def _tick(self) -> None:
        now = time.monotonic()
        dt, self._last = min(0.25, now - self._last), now
        self._eye_rows = self.eye.step()
        if self.eye.motion:
            self._phase += dt * math.tau * (1.4 if self.talking else 0.35)
            self.glow = round((math.sin(self._phase) + 1) / 2 * 12) / 12
        else:
            self.glow = 0.6
        self.refresh()

    def on_click(self, event: events.Click) -> None:
        x0, y0, x1, y1 = eyeart.box_geometry(self.size.width, self.size.height, self.eye.n)["eye"]
        for name, (row, a, b) in self.hits.items():
            if event.y == row and a <= event.x <= b:
                self.post_message(self.ReadoutClicked(name, event.screen_x - (event.x - a), event.screen_y + 1))
                break
        else:
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                self.eye.click()
            else:
                self.query_one(TypingArea).focus()
        event.stop()

    def render(self) -> Text:
        w, h, n = self.size.width, self.size.height, self.eye.n
        out = Text(no_wrap=True, overflow="crop")
        if w < eyeart.eye_width(n) + 12 or h < n // 4 + 3:
            return out
        g = eyeart.box_geometry(w, h, n)
        tx0, ty0, tx1, ty1 = g["text"]
        tw = tx1 - tx0 + 1
        bt_row = g["box_top_px"] // 2
        ew = g["eye_w"]
        # title and subtitle sit skin.title_indent cells right of the eye; the MODEL line keeps 5 (the mount is there)
        hud = {}
        for i, line in enumerate(self.hud_lines()):
            if bt_row - 3 + i >= 0:
                hx = ew + (self.skin.title_indent if i < 2 else 5)
                hud[bt_row - 3 + i] = (hx, w - hx - 1, line)
        hits = {}
        colors = self.skin.box_colors()
        px = eyeart.dialogue_pixels(w, h, n, self.glow, self._eye_rows, colors)
        bg = colors["background"]
        blank = style((255, 255, 255), colors["box_fill"])
        prompt = style(colors["rim_bright"], colors["box_fill"], bold=True)
        for r in range(h):
            top, bot = px[2 * r], px[2 * r + 1]
            x = 0
            while x < w:
                if r in hud and x == hud[r][0] and hud[r][1] > 0:
                    used, room = 0, hud[r][1]
                    for txt, st, name in hud[r][2]:
                        take = txt[: max(0, room - used)]
                        if take:
                            if name:
                                hits[name] = (r, x + used, x + used + cell_len(take) - 1)
                            out.append(take, st)
                            used += cell_len(take)
                    if used:
                        x += used
                        continue
                if ty0 <= r <= ty1 and x == tx0:
                    out.append("▸ " if r == ty0 else "  ", prompt)
                    out.append(" " * (tw - 2), blank)
                    x += tw
                    continue
                t, b = top[x], bot[x]
                if t is None and b is None:
                    out.append(" ", style(bg, bg))
                else:
                    out.append("▀", style(t or bg, b or bg))
                x += 1
            if r < h - 1:
                out.append("\n")
        self.hits = hits
        return out


class ChoiceMenu(OptionList):
    """A small dropdown floating over the chat at (x, y): an overlay, not a separate screen, so the eye keeps
    moving behind it. Posts Chosen(key, value) with the picked value, or None (Esc / clicked away)."""

    BINDINGS = [("escape", "close", "Close")]
    DEFAULT_CSS = """
    ChoiceMenu { overlay: screen; position: absolute; width: auto; max-width: 40; height: auto; padding: 0; }
    """

    class Chosen(Message):
        def __init__(self, key: str, value: str | None) -> None:
            super().__init__()
            self.key, self.value = key, value

    def __init__(self, key: str, choices: list, current: str, x: int, y: int) -> None:
        """choices: [(value, label)]."""
        super().__init__(*[Option(("● " if v == current else "  ") + label, id=v) for v, label in choices])
        self.key, self.values, self.current, self.at, self._done = key, [v for v, _ in choices], current, (x, y), False

    def on_mount(self) -> None:
        x, y = self.at
        w, h = self.screen.size
        rows = len(self.values) + 2
        if y + rows > h:
            y = max(0, y - rows - 1)
        width = max(cell_len(str(o.prompt)) for o in self._options) + 4
        self.styles.offset = (max(0, min(x - 1, w - width)), y)
        if self.current in self.values:
            self.highlighted = self.values.index(self.current)
        self.focus()

    def close(self, value: str | None) -> None:
        if not self._done:
            self._done = True
            self.post_message(self.Chosen(self.key, value))
            self.remove()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.close(event.option.id)

    def on_blur(self) -> None:
        self.close(None)

    def action_close(self) -> None:
        self.close(None)
