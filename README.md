# spaceManUI

A skinnable, animated terminal interface for [Qwen Code](https://github.com/QwenLM/qwen-code).

A pixel-art ship's-computer eye sits on a glowing console at the bottom of your terminal and reacts to what
the agent is doing. It thinks, scans, writes and waits for your approval, and its colour changes with each
state. Above the console, the conversation streams in with collapsible thinking, tool cards, diffs, real
charts and inline images.

spaceManUI is only the interface. Qwen Code still does all the work: your model, settings, sign-in, tools,
MCP servers, extensions and saved sessions are the ones you already have. spaceManUI starts `qwen --acp` and
talks to it over the [Agent Client Protocol](https://agentclientprotocol.com), the same way editors like Zed do.

*Not affiliated with Alibaba or the Qwen team.*

## Requirements

- Qwen Code installed and signed in: `npm install -g @qwen-code/qwen-code`, then run `qwen` once.
  Tested with Qwen Code 0.28.4.
- Python 3.10 or newer.
- A terminal with true colour. For real pictures (not blocky ones), use a terminal with an image protocol:
  Kitty, WezTerm, iTerm2, Ghostty, or Windows Terminal 1.22+ (Sixel). Other terminals fall back to half-block images.

## Install

```sh
uv tool install git+https://github.com/MillerAJ/spaceManUI
# or
pipx install git+https://github.com/MillerAJ/spaceManUI
```

Then, in any project folder:

```sh
spaceman
```

## Using it

| Key | What it does |
|---|---|
| Enter | send |
| Shift+Enter, Alt+Enter, Ctrl+J | new line |
| Esc | stop the current reply |
| y / a / u / n | on a permission request: allow once / always in this project / always for you / reject |
| F3 | cycle the thinking effort |
| F4 | cycle the approval mode (plan, default, auto-edit, auto, yolo) |
| Ctrl+R | pick up a saved conversation |
| Ctrl+N | new conversation |
| F2 | settings (skin, sounds, animation, show thinking) |
| Ctrl+P | command palette |
| Ctrl+Q | quit |

The **MODE** and **THINK** readouts next to the eye are clickable menus. Click the eye to make it blink, or
click it three times fast.

Conversations are Qwen Code's own. A session started in `qwen` can be picked up in spaceManUI and the other way
round (`/resume` in `qwen`, Ctrl+R here). Slash commands that Qwen Code supports over ACP work when typed here too.

```text
spaceman                      start a new conversation in this folder
spaceman -c                   pick up the most recent one
spaceman -r <session-id>      pick up a specific one
spaceman --mode plan          start in plan mode (also: default, auto-edit, auto, yolo)
spaceman --effort low         start with low thinking effort
spaceman --skin <name>        use a different skin
spaceman --agent "qwen --acp --model x"   run the agent with extra flags
```

If your agent refers to files through a URL prefix (for example `/files/` served by a web front end), map it
to a folder so the pictures show up: `SPACEMAN_PATH_MAP="/files/=/path/to/workspace/"`. Separate several
pairs like `PATH`.

## Extras (opt-in)

```sh
spaceman ext install display     # teach the agent to draw charts and show images here (recommended)
spaceman ext install navigator   # a calm ship's-computer voice to go with the default skin
spaceman look apply              # make the stock `qwen` terminal match (theme, banner, status line)
```

- **display** is a tiny Qwen Code extension, just a context file. It tells the agent that a fenced ` ```chart `
  block with JSON becomes a real chart, and that mentioning an image path shows the picture.
- **navigator** is a voice, also a context file. Remove it with `spaceman ext remove navigator`.
- **look** edits `~/.qwen/settings.json` after making a timestamped backup. `spaceman look remove` puts your
  previous look back.

Add `--scope project` to the `ext` commands to enable an extension for the current project only.

### Charts

````text
```chart
{"type": "bar", "title": "Builds per day", "x": ["Mon", "Tue", "Wed"], "y": [4, 7, 3]}
```
````

`type` can be `line`, `scatter`, `bar` or `hbar`. For several series, use
`"series": [{"name": ..., "x": [...], "y": [...]}]`.

## Skins

The look and the words are a skin: TOML colours, the eye's colour for each state, the readout labels and the
status words. The built-in skin is **Navigator**. To make your own:

```sh
spaceman skins            # lists skins and shows where yours go
```

Copy the built-in `navigator.toml` into that folder under a new name and change what you like. A skin only
needs the keys it changes; everything else comes from Navigator. Then choose it in F2 settings, or use
`spaceman --skin yourname`. You can add your own iris colours under `[palettes.<name>]`.

## How it works

```
spaceman (Textual app) ──JSON-RPC over stdio──▶ qwen --acp ──▶ your model
     ▲   eye + console + log                           │
     └──── session/update: thoughts, text, tool calls, │
           permission requests, usage, mode changes ◀──┘
```

- `src/spaceman/acp.py`: a small dependency-free ACP client.
- `src/spaceman/app.py`: the app; it turns each update into the log and the eye's state.
- `src/spaceman/eye/`: the pixel-art eye and console. `eyeart.py` is pure drawing; `widget.py` animates it.
- `src/spaceman/messages.py`: bubbles, thinking, tool cards, permission prompts, charts, images.
- `src/spaceman/skins/`: the skin loader and the built-in skin.

spaceManUI opens no network ports and sends nothing anywhere. It only talks to the `qwen` process it starts.
The agent's stderr is logged locally (`spaceman paths` shows where).

## Development

```sh
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

The tests drive the real app headless against `tests/fake_agent.py`, which speaks the same ACP messages
Qwen Code sends. Those messages were copied from a recorded session.

## License

MIT. See [LICENSE](LICENSE).
