"""Everything that appears in the conversation: your messages, replies, thoughts, tool cards, approvals,
charts and images."""
from __future__ import annotations

import asyncio
import base64
import difflib
import io
import json
import re
from pathlib import Path

from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Button, Collapsible, Static

CHART_RE = re.compile(r"```chart[ \t]*\n(.*?)```", re.S)
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
PATH_IMAGE_RE = re.compile(r"(?<![\w(/\\])((?:[A-Za-z]:)?[\w./\\~-]+\.(?:png|jpe?g|gif|webp|bmp))\b", re.I)
MAX_IMAGES = 4


class UserBubble(Static):
    def __init__(self, text: str) -> None:
        super().__init__(Text(text), classes="you")
        self.text = text

    def add(self, more: str) -> None:
        self.text += more
        self.update(Text(self.text))


class Notice(Static):
    """A one-line system note (errors, session loaded, ...)."""
    def __init__(self, text: str, kind: str = "info") -> None:
        super().__init__(Text(text), classes=f"notice {kind}")


class AgentBubble(Static):
    """A reply, streamed in as Markdown. finish() pulls out ```chart blocks and image references and shows
    them as real charts and pictures underneath."""

    def __init__(self) -> None:
        super().__init__("", classes="bot")
        self.text = ""
        self._dirty = False

    def add(self, chunk: str) -> None:
        self.text += chunk
        self._dirty = True

    def flush(self, cursor: str = "") -> None:
        if self._dirty or cursor:
            self._dirty = False
            self.update(Markdown(self.text.strip() + cursor))

    def finish(self, cwd: Path) -> list[Widget]:
        """Final render. Returns extra widgets (charts, images) to mount after the bubble."""
        extras: list[Widget] = []
        body = self.text
        for m in CHART_RE.finditer(body):
            chart = make_chart(m.group(1))
            if chart is not None:
                extras.append(chart)
        if extras:
            body = CHART_RE.sub("", body)
        for path in find_images(body, cwd):
            img = make_image(path)
            if img is not None:
                extras.append(img)
        self.update(Markdown(body.strip() or " "))
        return extras


class ThoughtBlock(Collapsible):
    """The model's reasoning, dim and collapsible. Open while it streams; folds away when the reply starts."""

    def __init__(self, title: str) -> None:
        self.body = Static("", classes="thought-text")
        super().__init__(self.body, title=title, collapsed=False, classes="thought")
        self.text = ""
        self._dirty = False

    def add(self, chunk: str) -> None:
        self.text += chunk
        self._dirty = True

    def flush(self) -> None:
        if self._dirty:
            self._dirty = False
            self.body.update(Text(self.text.strip()))


STATUS_ICON = {"pending": "◌", "in_progress": "◐", "completed": "✔", "failed": "✖"}
KIND_ICON = {"read": "📖", "edit": "✎", "delete": "🗑", "move": "⇄", "search": "🔍", "execute": "▶",
             "think": "◇", "fetch": "⇣", "other": "•"}


class ToolCard(Static):
    """One tool call: what it is, its status, and a short look at the result (or the diff, for edits)."""

    def __init__(self, call: dict, colors: dict) -> None:
        super().__init__("", classes="tool")
        self.call = dict(call)
        self.skin_colors = colors
        self.extras_done = False

    def merge(self, update: dict) -> None:
        for k, v in update.items():
            if k == "_meta" or v is None:
                continue
            if k == "content" and not v:
                continue
            self.call[k] = v
        self.redraw()

    def on_mount(self) -> None:
        self.redraw()

    def redraw(self) -> None:
        c = self.call
        status = c.get("status", "pending")
        colour = {"completed": self.skin_colors["tool_ok"], "failed": self.skin_colors["tool_fail"]}.get(status, self.skin_colors["tool"])
        out = Text()
        out.append(f"{STATUS_ICON.get(status, '•')} ", style=f"bold {colour}")
        out.append(f"{KIND_ICON.get(c.get('kind') or 'other', '•')} ", style=self.skin_colors["tool"])
        out.append(_one_line(c.get("title") or "tool", 160), style=f"bold {self.skin_colors['tool']}")
        for loc in (c.get("locations") or [])[:3]:
            if loc.get("path"):
                out.append(f"\n   {loc['path']}" + (f":{loc['line']}" if loc.get("line") else ""), style="dim")
        for block in c.get("content") or []:
            if block.get("type") == "diff":
                out.append("\n")
                out.append_text(render_diff(block.get("path", ""), block.get("oldText"), block.get("newText") or ""))
            elif block.get("type") == "content" and (block.get("content") or {}).get("type") == "text" and status in ("completed", "failed"):
                out.append("\n")
                out.append(_preview(block["content"].get("text", ""), 8), style="dim")
        self.update(out)

    def images(self) -> list[Widget]:
        """Pictures the tool returned (for example a screenshot), once the call is done."""
        if self.extras_done or self.call.get("status") not in ("completed", "failed"):
            return []
        self.extras_done = True
        found = []
        for block in self.call.get("content") or []:
            inner = block.get("content") if block.get("type") == "content" else block
            if (inner or {}).get("type") == "image" and inner.get("data"):
                img = make_image(io.BytesIO(base64.b64decode(inner["data"])))
                if img is not None:
                    found.append(img)
        return found[:MAX_IMAGES]


def _one_line(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _preview(text: str, lines: int) -> str:
    rows = text.rstrip().splitlines()
    shown = [("   " + r)[:200] for r in rows[:lines]]
    if len(rows) > lines:
        shown.append(f"   … {len(rows) - lines} more lines")
    return "\n".join(shown)


def render_diff(path: str, old: str | None, new: str, max_lines: int = 40) -> Text:
    out = Text()
    lines = list(difflib.unified_diff((old or "").splitlines(), new.splitlines(),
                                      fromfile=path or "a", tofile=path or "b", lineterm="", n=2))
    if not lines and old is None:
        lines = [f"+++ {path} (new file)"] + ["+" + l for l in new.splitlines()]
    for line in lines[:max_lines]:
        style = "green" if line.startswith("+") else "red" if line.startswith("-") else "cyan" if line.startswith("@@") else "dim"
        out.append("   " + line[:200] + "\n", style=style)
    if len(lines) > max_lines:
        out.append(f"   … {len(lines) - max_lines} more diff lines\n", style="dim")
    out.rstrip()
    return out


class PermissionPrompt(Vertical, can_focus=True):
    """Asks before a tool runs. One button per option the agent offers; keys: y allow once,
    a always allow (this project), u always allow (you, everywhere), n / Esc reject."""

    BINDINGS = [
        Binding("y", "pick('allow_once')", "Allow once"),
        Binding("a", "pick('allow_always')", "Always (project)"),
        Binding("u", "pick('allow_always_user')", "Always (user)"),
        Binding("n", "pick('reject_once')", "Reject"),
        Binding("escape", "pick('reject_once')", "Reject", show=False),
    ]

    def __init__(self, params: dict) -> None:
        super().__init__(classes="permission")
        self.params = params
        self.options = params.get("options") or []
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()

    def compose(self) -> ComposeResult:
        call = self.params.get("toolCall") or {}
        raw = call.get("rawInput") or {}
        title = Text("⚠ Permission needed  ", style="bold")
        title.append(_one_line(call.get("title") or "a tool", 200))
        yield Static(title, classes="permission-title")
        detail = raw.get("command") or raw.get("file_path") or raw.get("path") or raw.get("url")
        if detail:
            yield Static(Text("   " + str(detail)[:600], style="bold"), classes="permission-detail")
        for block in call.get("content") or []:
            if block.get("type") == "diff":
                yield Static(render_diff(block.get("path", ""), block.get("oldText"), block.get("newText") or "", 30))
        with Horizontal(classes="permission-buttons"):
            for i, opt in enumerate(self.options):
                key = self._key_for(opt)
                label = (f"[{key}] " if key else "") + opt.get("name", opt.get("optionId", "?"))
                variant = "error" if opt.get("kind", "").startswith("reject") else "success" if opt.get("kind") == "allow_once" else "primary"
                yield Button(label, id=f"opt-{i}", variant=variant, compact=True)

    def _key_for(self, opt: dict) -> str:
        kind, oid = opt.get("kind", ""), opt.get("optionId", "")
        if kind == "allow_once":
            return "y"
        if kind.startswith("reject"):
            return "n"
        if kind == "allow_always":
            return "u" if oid.endswith("_user") else "a"
        return ""

    def on_mount(self) -> None:
        self.focus()
        self.scroll_visible()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self._resolve(self.options[int(event.button.id.split("-")[1])])

    def action_pick(self, what: str) -> None:
        for opt in self.options:
            kind, oid = opt.get("kind", ""), opt.get("optionId", "")
            if what == "allow_always_user" and kind == "allow_always" and oid.endswith("_user"):
                return self._resolve(opt)
            if what == "allow_always" and kind == "allow_always" and not oid.endswith("_user"):
                return self._resolve(opt)
            if what in ("allow_once", "reject_once") and kind == what:
                return self._resolve(opt)

    def _resolve(self, opt: dict | None) -> None:
        if not self.future.done():
            self.future.set_result(opt.get("optionId") if opt else None)
        chosen = opt.get("name", "") if opt else "cancelled"
        self.remove_children()
        self.mount(Static(Text(f"⚑ {chosen}", style="dim")))
        self.can_focus = False

    def cancel(self) -> None:
        self._resolve(None)


# --- charts --------------------------------------------------------------------------------------
def make_chart(spec_text: str) -> Widget | None:
    """A ```chart block: JSON like
         {"type": "line", "title": "Speed", "x": [1, 2, 3], "y": [3, 5, 4], "xlabel": "...", "ylabel": "..."}
       or several series: {"type": "bar", "series": [{"name": "a", "x": [...], "y": [...]}, ...]}
       type: line, scatter, bar, hbar. Bad JSON just stays as text."""
    try:
        spec = json.loads(spec_text)
    except ValueError:
        return None
    if not isinstance(spec, dict):
        return None
    from textual_plotext import PlotextPlot

    class Chart(PlotextPlot):
        def on_mount(self) -> None:
            plt = self.plt
            kind = spec.get("type", "line")
            series = spec.get("series") or [{"name": None, "x": spec.get("x"), "y": spec.get("y")}]
            for s in series:
                y = s.get("y") or []
                x = s.get("x") or list(range(1, len(y) + 1))
                label = s.get("name")
                if kind == "bar":
                    plt.bar(x, y, label=label)
                elif kind == "hbar":
                    plt.bar(x, y, label=label, orientation="horizontal")
                elif kind == "scatter":
                    plt.scatter(x, y, label=label)
                else:
                    plt.plot(x, y, label=label)
            if spec.get("title"):
                plt.title(str(spec["title"]))
            if spec.get("xlabel"):
                plt.xlabel(str(spec["xlabel"]))
            if spec.get("ylabel"):
                plt.ylabel(str(spec["ylabel"]))

    try:
        return Chart(classes="chart")
    except Exception:
        return None


# --- images ----------------------------------------------------------------------------------------
def find_images(text: str, cwd: Path) -> list[Path]:
    found: list[Path] = []
    candidates = MD_IMAGE_RE.findall(text) + PATH_IMAGE_RE.findall(text)
    for raw in candidates:
        if raw.startswith(("http://", "https://", "data:")):
            continue
        raw = raw.removeprefix("file://")
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = cwd / p
        try:
            if p.is_file() and p not in found:
                found.append(p)
        except OSError:
            continue
        if len(found) >= MAX_IMAGES:
            break
    return found


def make_image(source) -> Widget | None:
    try:
        from textual_image.widget import Image as ImageWidget
        return ImageWidget(source, classes="picture")
    except Exception:
        return None
