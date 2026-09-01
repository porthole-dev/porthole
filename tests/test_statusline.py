#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole statusline`: whether to draw a row, and where --install writes.

The bar itself is `porthole_progress.line_of` and is covered by
tests/test_progress.py. What is new here is the DECISION -- whether a row is
drawn at all -- which is the whole reason the second line is status rather
than permanent furniture, and the install path, which writes into somebody
else's git repo and must not dirty it.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import porthole_cmd_statusline as sl  # noqa: E402


class _FakeOut:
    def __init__(self):
        self.lines = []

    def __call__(self, text=""):
        self.lines.append(text)

    def paint(self, s, _c):
        return s

    def blank(self):
        self.lines.append("")

    def hint(self, text):
        self.lines.append(text)


class _FakeCtx:
    def __init__(self):
        self.out = _FakeOut()
        self.payload = None

    def emit(self, payload, render):
        self.payload = payload
        render()
        return 0


class _Args:
    def __init__(self, **kw):
        self.install = True
        self.project = None
        self.json = False
        self.__dict__.update(kw)


def _fake_repo(tmp: pathlib.Path) -> pathlib.Path:
    """A tree that looks like a porthole checkout to build_line."""
    (tmp / "lib").symlink_to(ROOT / "lib")
    (tmp / ".run").mkdir()
    return tmp


def _write(repo, snap):
    (repo / ".run" / "build-status.json").write_text(json.dumps(snap))


def test_no_status_file_draws_no_row():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(pathlib.Path(tmp))
        assert sl.build_line(repo, 100, 1000.0) is None


def test_a_running_build_draws_its_bar():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(pathlib.Path(tmp))
        now = 1000.0
        _write(repo, {"rung": "fast", "phase": "make", "state": "running",
                      "pid": os.getpid(), "elapsed": 12.0, "progress": 0.5,
                      "eta": 30.0, "last": "  CC drivers/foo.o",
                      "last_at": now})
        row = sl.build_line(repo, 100, now)
        assert row and "fast" in row and "50%" in row, row


def test_a_recent_failure_is_still_worth_a_row():
    """The result is the most interesting moment; the row must not vanish at
    exactly the point the human wants to read it."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(pathlib.Path(tmp))
        now = 1000.0
        _write(repo, {"rung": "fast", "phase": "flash", "state": "failed",
                      "pid": os.getpid(), "elapsed": 12.0, "progress": None,
                      "eta": None, "last": "boom", "last_at": now - 10})
        row = sl.build_line(repo, 100, now)
        assert row and "failed" in row, row


def test_an_old_result_expires():
    """The positive control for every assertion above: without this the row
    is permanent furniture nobody reads, and 'it draws a row' would pass for
    a function that always drew one."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(pathlib.Path(tmp))
        now = 1000.0
        _write(repo, {"rung": "fast", "phase": "flash", "state": "failed",
                      "pid": os.getpid(), "elapsed": 12.0, "progress": None,
                      "eta": None, "last": "boom",
                      "last_at": now - sl.LINGER_S - 1})
        assert sl.build_line(repo, 100, now) is None


def test_the_settings_carry_a_refresh_interval():
    """Without it the line re-runs only on session events, and a detached
    build advancing while the model is idle produces none -- the bar would
    freeze at whatever it said when the agent last spoke."""
    line = sl.SETTINGS["statusLine"]
    assert line["refreshInterval"] >= 1, line
    # No path in the command: the same JSON has to be right on every machine.
    assert line["command"] == "porthole statusline", line
    assert "/" not in line["command"], line


def _git(project, *args, env=None):
    base = dict(os.environ)
    base.update(env or {})
    return subprocess.run(["git", "-C", str(project), *args],
                          capture_output=True, text=True, env=base)


def test_install_writes_local_settings_and_does_not_dirty_the_repo():
    """A kernel tree is somebody's repo. A bar in an agent's chrome is a
    personal preference, so it goes in settings.local.json and gets ignored
    through .git/info/exclude -- never through the tracked .gitignore."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = pathlib.Path(tmp) / "tree"
        proj.mkdir()
        # An ISOLATED git: this developer's own ~/.config/git/ignore already
        # ignores .claude/settings.local.json, which would mask the exclude
        # path entirely and pass without ever running it.
        home = pathlib.Path(tmp) / "home"
        home.mkdir()
        env = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
               "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        os.environ.update(env)
        try:
            _git(proj, "init", "-q")
            ctx = _FakeCtx()
            assert sl._install(ctx, _Args(project=str(proj))) == 0

            target = proj / sl.LOCAL_SETTINGS
            assert json.loads(target.read_text())["statusLine"] == \
                sl.SETTINGS["statusLine"]

            exclude = proj / ".git" / "info" / "exclude"
            assert exclude.exists() and sl.LOCAL_SETTINGS.as_posix() \
                in exclude.read_text()
            # .gitignore is the tracked file and must be untouched.
            assert not (proj / ".gitignore").exists()
            assert _git(proj, "status", "--short").stdout.strip() == ""
        finally:
            for key in env:
                os.environ.pop(key, None)


def test_install_merges_rather_than_overwrites():
    """settings.local.json is where a person keeps their own permissions and
    model choice. Eating those would cost more than this feature is worth."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = pathlib.Path(tmp)
        target = proj / sl.LOCAL_SETTINGS
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({"model": "opus", "permissions": {"a": 1}}))
        ctx = _FakeCtx()
        assert sl._install(ctx, _Args(project=str(proj))) == 0
        got = json.loads(target.read_text())
        assert got["model"] == "opus" and got["permissions"] == {"a": 1}, got
        assert got["statusLine"] == sl.SETTINGS["statusLine"], got


def test_install_refuses_settings_it_cannot_parse():
    """Rather than overwrite them. --install is a one-shot a human is
    watching, so unlike the render path it fails loudly."""
    from porthole_cli import Bail
    with tempfile.TemporaryDirectory() as tmp:
        proj = pathlib.Path(tmp)
        target = proj / sl.LOCAL_SETTINGS
        target.parent.mkdir(parents=True)
        target.write_text("{ not json")
        try:
            sl._install(_FakeCtx(), _Args(project=str(proj)))
        except Bail as exc:
            assert "valid JSON" in exc.message, exc.message
            assert target.read_text() == "{ not json", "it overwrote anyway"
            return
        raise AssertionError("a malformed settings file was not refused")


# ----------------------------------------------- it appends, never replaces --
#
# The first version REPLACED. A settings file holds one statusLine and a
# project one shadows the user one, so installing into a project silently
# swapped a three-line display -- rate limits with reset times, path and
# branch, the session's skills -- for a worse copy of its first line. These
# are the tests that would have caught it.

def _base(tmp, name, command):
    home = pathlib.Path(tmp) / ".claude"
    home.mkdir(parents=True, exist_ok=True)
    (home / name).write_text(json.dumps(
        {"statusLine": {"type": "command", "command": command}}))
    return home


def test_the_inherited_command_is_discovered_from_user_settings():
    with tempfile.TemporaryDirectory() as tmp:
        home = _base(tmp, "settings.json", "bash ~/.claude/statusline.sh")
        os.environ["CLAUDE_CONFIG_DIR"] = str(home)
        os.environ.pop("PORTHOLE_STATUSLINE_BASE", None)
        try:
            assert sl._base_command() == "bash ~/.claude/statusline.sh"
            # local wins, matching the harness's own precedence
            _base(tmp, "settings.local.json", "echo LOCAL")
            assert sl._base_command() == "echo LOCAL"
        finally:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)


def test_a_user_level_porthole_statusline_is_not_chained_to_itself():
    """Otherwise it forks itself once every two seconds, forever."""
    with tempfile.TemporaryDirectory() as tmp:
        home = _base(tmp, "settings.json", "porthole statusline")
        os.environ["CLAUDE_CONFIG_DIR"] = str(home)
        os.environ.pop("PORTHOLE_STATUSLINE_BASE", None)
        try:
            assert sl._base_command() == ""
        finally:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)


def _render(env):
    base = dict(os.environ)
    base.update({"PATH": os.environ["PATH"], "COLUMNS": "100"})
    base.update(env)
    return subprocess.run(
        [sys.executable, str(ROOT / "bin" / "porthole"), "statusline"],
        input='{"model":{"display_name":"M"},"workspace":{"current_dir":"'
              + str(ROOT) + '"}}',
        capture_output=True, text=True, env=base).stdout


def test_the_inherited_output_is_passed_through_and_not_supplanted():
    out = _render({"PORTHOLE_STATUSLINE_BASE": "printf 'MINE-1\\nMINE-2\\n'"})
    assert "MINE-1" in out and "MINE-2" in out, out
    # THE REGRESSION CONTROL: porthole's own line must not appear alongside a
    # status line somebody already has. That substitution is the whole bug.
    assert "% ctx" not in out, out


def test_a_fresh_install_with_nothing_to_chain_still_says_something():
    out = _render({"PORTHOLE_STATUSLINE_BASE": ""})
    assert "% ctx" not in out or "M" in out, out
    assert out.strip(), "no inherited line and no fallback: an empty status row"


def test_a_base_that_prints_nothing_falls_back_rather_than_going_blank():
    out = _render({"PORTHOLE_STATUSLINE_BASE": "true"})
    assert out.strip(), "a silent inherited command left an empty status row"


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
