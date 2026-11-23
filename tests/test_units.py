import json

from spaceman import cli, skins
from spaceman.messages import find_images, make_chart, render_diff


def test_builtin_skin_complete():
    s = skins.load(None)
    assert s.name == "Navigator"
    for key in ("background", "box_fill", "rim_dim", "rim_bright", "wait_border", "hud_title"):
        assert s.rgb(key)
    assert s.tool_mode("execute") == "execute" and s.tool_mode(None) == "think"
    assert s.palette("search")["edge"]


def test_user_skin_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACEMAN_HOME", str(tmp_path))
    (tmp_path / "skins").mkdir()
    (tmp_path / "skins" / "mine.toml").write_text(
        'title = "MINE"\n[eye]\nidle = "violet"\n[palettes.violet]\nedge="#b48cff"\nmid="#7d4fd6"\ndeep="#2e1866"\nlight="#e6d9ff"\n',
        encoding="utf-8")
    assert "mine" in skins.available()
    s = skins.load("mine")
    assert s.title == "MINE" and s.subtitle == "SHIP'S COMPUTER"
    assert s.palette("idle")["edge"] == (0xb4, 0x8c, 0xff)


def test_bad_skin_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACEMAN_HOME", str(tmp_path))
    assert skins.load("does-not-exist").name == "Navigator"


def test_find_images(tmp_path):
    (tmp_path / "plot.png").write_bytes(b"x")
    found = find_images("Saved ![plot](plot.png) and https://x/y.png and missing.png", tmp_path)
    assert found == [tmp_path / "plot.png"]


def test_path_map(tmp_path, monkeypatch):
    (tmp_path / "ws" / "d").mkdir(parents=True)
    (tmp_path / "ws" / "d" / "x.png").write_bytes(b"x")
    monkeypatch.setenv("SPACEMAN_PATH_MAP", f"/files/={tmp_path / 'ws'}/")
    assert find_images("see ![d](/files/d/x.png)", tmp_path) == [tmp_path / "ws" / "d" / "x.png"]


def test_chart_spec():
    assert make_chart('{"type": "bar", "x": ["a", "b"], "y": [1, 2]}') is not None
    assert make_chart("not json") is None


def test_diff_render():
    out = render_diff("f.py", "a\nb\n", "a\nc\n").plain
    assert "-b" in out and "+c" in out


def test_look_apply_and_remove(tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_HOME", str(tmp_path))
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": {"name": "x"}, "ui": {"theme": "Qwen Dark", "hideTips": False}}))
    assert cli.main(["look", "apply"]) == 0
    applied = json.loads(settings.read_text(encoding="utf-8"))
    assert applied["ui"]["theme"] == "Navigator" and applied["model"] == {"name": "x"}
    assert cli.main(["look", "remove"]) == 0
    restored = json.loads(settings.read_text(encoding="utf-8"))
    assert restored["ui"] == {"theme": "Qwen Dark", "hideTips": False}
    assert list(tmp_path.glob("settings.json.bak-spaceman-*"))
