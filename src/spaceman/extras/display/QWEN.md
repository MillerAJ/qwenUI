# Display hints for spaceManUI

The user may be reading your replies in spaceManUI, a terminal interface that can show more than text.

## Charts
To show a chart, put a fenced code block with the language `chart` containing JSON. It is drawn as a real
chart under your reply; the JSON itself is hidden. Use it when numbers are easier to see than to read.

```chart
{"type": "line", "title": "Requests per hour", "x": [1, 2, 3, 4], "y": [120, 180, 90, 210],
 "xlabel": "hour", "ylabel": "requests"}
```

- `type`: `line`, `scatter`, `bar` or `hbar`.
- One series: `x` and `y` lists (`x` is optional). Several: `"series": [{"name": "a", "x": [...], "y": [...]}, ...]`.
- Bar charts may use text labels for `x`. Keep it to a few hundred points at most.

## Images
If you create or find an image file on this machine (PNG, JPG, GIF, WEBP, BMP), mention its path in your reply,
ideally as Markdown: `![what it shows](path/to/file.png)`. spaceManUI shows the picture under your reply.
Paths may be absolute or relative to the working directory. Web URLs are not fetched.
