#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole statusline`: whether to draw a row, and where --install writes.

The bar itself is `porthole_progress.line_of` and is covered by
tests/test_progress.py. What is new here is the DECISION -- whether a row is
drawn at all -- which is the whole reason the second line is status rather
than permanent furniture, and the install path, which writes into somebody
else's git repo and must not dirty it.
"""
import contextlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402
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


@contextlib.contextmanager
def _only_these_logs(*dirs):
    """Point every pmbootstrap work dir at a temp one.

    Both of them, or the machine's own log.txt -- which on a porting host is
    being written all the time -- decides the test.
    """
    saved = {name: os.environ.get(name) for name, _ in sl.PMB_LOGS}
    for index, (name, _) in enumerate(sl.PMB_LOGS):
        os.environ[name] = str(dirs[min(index, len(dirs) - 1)])
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


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


def test_a_build_that_outlived_its_tracker_still_gets_a_row():
    """Measured on this host: a webkit build ran for two hours at 88% with a
    status line that said nothing, because the porthole run that published
    .run/pkg-status.json had been killed and `liveness` therefore called the
    snapshot stale. The workspace log was being appended to the whole time."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(pathlib.Path(tmp))
        now = 1000.0
        pmb = pathlib.Path(tmp) / "pmb"
        pmb.mkdir()
        (pmb / "log.txt").write_text(
            "[8100/9429] Building CXX object a.cpp.o\n"
            "[8522/9429] Building CXX object b.cpp.o\n")
        os.utime(pmb / "log.txt", (now - 3, now - 3))
        # frozen mid-build: state says running, the pid is long gone
        _write(repo, {"rung": "pkg:webkit2gtk-6.0", "phase": "build",
                      "state": "running", "pid": 999999999, "elapsed": 6832.0,
                      "progress": 0.839, "eta": None, "last": "[7907/9429]",
                      "last_at": now - 2900, "started": now - 9700})
        with _only_these_logs(pmb):
            snap, reattached = sl.build_snapshot(repo, now)
            row = sl.build_line(repo, 100, now)
    assert reattached and snap is not None
    # the LOG's numbers, not the frozen file's 83.9%
    assert snap["steps"] == "8522/9429", snap
    assert "webkit2gtk-6.0" in row and "90%" in row, row
    assert "reattached" in row, row


def test_a_build_that_failed_overnight_does_not_still_read_as_running():
    """Reported with a screenshot: `webkit2gtk-6.0 [====] 99% 9428/9429 .
    reattached`, still on screen the next morning. The build had failed at
    23:59; every unrelated `pmbootstrap chroot` since had refreshed the one
    mtime that was keeping it there, and the row has no clock of its own to
    contradict it with."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(pathlib.Path(tmp))
        now = time.time()
        failed_at = now - 8 * 3600
        stamp = time.strftime("%H:%M:%S", time.localtime(failed_at))
        pmb = pathlib.Path(tmp) / "pmb"
        pmb.mkdir()
        (pmb / "log.txt").write_text(
            "[9428/9429] Generating WebKitWebProcessExtension-6.0.typelib\n"
            ">>> ERROR: webkit2gtk-6.0: build failed\n"
            f"(246720) [{stamp}] ERROR: Couldn't build webkit2gtk-6.0.apk!\n"
            f"(328393) [{time.strftime('%H:%M:%S')}] DONE!\n")
        os.utime(pmb / "log.txt", (now - 10, now - 10))   # touched just now
        _write(repo, {"rung": "pkg:webkit2gtk-6.0", "phase": "build",
                      "state": "running", "pid": 999999999, "elapsed": 6832.0,
                      "progress": 0.839, "eta": None, "last": "[7907/9429]",
                      "last_at": now - 36000, "started": now - 42000})
        with _only_these_logs(pmb):
            assert sl.build_line(repo, 100, now) is None

            # ...but the same failure, minutes old, is exactly what the row
            # is for: it is the result somebody has been waiting hours for.
            just_now = time.strftime("%H:%M:%S", time.localtime(now - 60))
            (pmb / "log.txt").write_text(
                "[9428/9429] Generating WebKitWebProcessExtension-6.0.typelib\n"
                ">>> ERROR: webkit2gtk-6.0: build failed\n"
                f"(246720) [{just_now}] ERROR: Couldn't build it!\n")
            os.utime(pmb / "log.txt", (now - 10, now - 10))
            row = sl.build_line(repo, 100, now)
    assert row and "webkit2gtk-6.0" in row and "failed" in row, row
    assert "99%" not in row, row


def test_an_unrelated_checkout_does_not_claim_the_machines_build():
    """The log belongs to the machine's workspace, not to this repo. A fresh
    one on its own says only "something is building somewhere", whose name
    could only be guessed -- so it takes a frozen `running` snapshot HERE to
    make those numbers this repo's build."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(pathlib.Path(tmp))
        now = 1000.0
        pmb = pathlib.Path(tmp) / "pmb"
        pmb.mkdir()
        (pmb / "log.txt").write_text("[10/20] Building CXX object a.cpp.o\n")
        os.utime(pmb / "log.txt", (now - 3, now - 3))
        with _only_these_logs(pmb):
            assert sl.build_line(repo, 100, now) is None
            # ...and a FINISHED build here does not adopt them either.
            _write(repo, {"rung": "fast", "state": "done", "pid": os.getpid(),
                          "elapsed": 5.0, "progress": 1.0, "eta": 0.0,
                          "last": "DONE!", "last_at": now - 9000})
            assert sl.build_line(repo, 100, now) is None


def test_the_row_is_coloured_even_though_stdout_is_not_a_terminal():
    """The status line is chrome the harness paints, not a pipe somebody is
    capturing -- `detect_style` would see stdout is not a tty and turn colour
    off, which is right everywhere else and wrong here. NO_COLOR still wins,
    and colour must never change how wide the row is."""
    import porthole_progress as pp
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(pathlib.Path(tmp))
        now = 1000.0
        _write(repo, {"rung": "fast", "phase": "make", "state": "running",
                      "pid": os.getpid(), "elapsed": 12.0, "progress": 0.5,
                      "eta": 30.0, "last": "  CC drivers/foo.o",
                      "last_at": now, "steps": "10/20"})
        row = sl.build_line(repo, 100, now)
        assert "\033[" in row, row
        assert pp.visible_len(row) <= 100
        saved = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"
        try:
            plain = sl.build_line(repo, 100, now)
        finally:
            if saved is None:
                del os.environ["NO_COLOR"]
            else:
                os.environ["NO_COLOR"] = saved
        assert "\033[" not in plain, plain
        assert pp.visible_len(row) == len(plain)


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
    return _runner.run(globals())


# ------------------------------------------ a build nobody published a file for --

def test_a_build_nobody_tracked_still_reaches_the_status_line():
    """Reported 2026-09-06: two hours of webkit building, an empty status line.

    The tracked snapshot said `done` -- a PREVIOUS build had finished and
    written it -- and the reattach path refuses anything whose snapshot is not
    `running`, so a build started outside `porthole pkg` had no way to be
    seen. `pkg watch` showed it only because it can afford a podman `ps`, and
    at a two-second refresh this cannot.

    Both file reads are faked here: the point is that a `done` snapshot plus a
    fresh log plus a named buildroot is enough, without asking a container
    anything.
    """
    import time
    work = pathlib.Path(tempfile.mkdtemp(prefix="porthole-sl-"))
    build = work / "chroot_buildroot_aarch64" / "home" / "pmos" / "build"
    build.mkdir(parents=True)
    (build / "APKBUILD").write_text("pkgname=webkit2gtk-6.0\n")
    (work / "log.txt").write_text(
        "[7947/9429] Building CXX object Source/WebCore/x.o\n")

    repo = pathlib.Path(tempfile.mkdtemp(prefix="porthole-sl-repo-"))
    (repo / ".run").mkdir()
    (repo / ".run" / "pkg-status.json").write_text(json.dumps(
        {"state": "done", "rung": "pkg:something-else",
         "last_at": time.time() - 9000, "pid": 1}))

    saved = os.environ.get("PORTHOLE_SANDBOX_PMB_DIR")
    os.environ["PORTHOLE_SANDBOX_PMB_DIR"] = str(work)
    try:
        snap, reattached = sl.build_snapshot(repo, time.time())
    finally:
        if saved is None:
            os.environ.pop("PORTHOLE_SANDBOX_PMB_DIR", None)
        else:
            os.environ["PORTHOLE_SANDBOX_PMB_DIR"] = saved

    assert snap is not None, "a live build was invisible to the status line"
    assert snap["rung"] == "pkg:webkit2gtk-6.0", snap["rung"]
    assert snap["state"] == "running", snap["state"]
    assert reattached is True


def test_a_failed_rung_does_not_resurrect_the_buildroot_s_last_package():
    """Reported twice on 2026-09-08 with screenshots: `webkit2gtk-6.0
    [ unknown ] -- 42h05m  build . reattached` in the status line while
    `porthole build fast` was the thing actually running -- and failing.

    Every input was behaving except one. The shared log was fresh because the
    KERNEL rung was writing it; the buildroot still staged a webkit APKBUILD
    from two days earlier, because a staged APKBUILD outlives its build; and
    `names_a_build` -- the guard that exists to stop exactly this pair from
    inventing a row -- said yes, on the strength of `>>> ERROR: failed to
    sign`. That is abuild's diagnostic, printed here by `abuild-sign` while
    pmbootstrap indexed a repo, and it was the ONLY build-shaped line in the
    whole tail.

    The dates are the tell and they were checked: staged 2026-09-06 15:08,
    screenshot 2026-09-08 08:51, difference 41h43m, which is what the row
    said.
    """
    import time
    now = time.time()
    long_ago = now - 42 * 3600

    work = pathlib.Path(tempfile.mkdtemp(prefix="porthole-sl-"))
    build = work / "chroot_buildroot_aarch64" / "home" / "pmos" / "build"
    build.mkdir(parents=True)
    (build / "APKBUILD").write_text("pkgname=webkit2gtk-6.0\n")
    os.utime(build / "APKBUILD", (long_ago, long_ago))
    # The real tail of the failed kernel rung, abridged. No ninja step, no
    # compile line, no package banner -- one `>>> ERROR:` from abuild-sign.
    (work / "log.txt").write_text(
        "(336465) [06:32:01] (native) index edge/rejected repository\n"
        "Can't open \".SIGN.RSA.pmos@local.rsa.pub\" for writing, "
        "Permission denied\n"
        ">>> ERROR: failed to sign \n"
        "mv: can't rename 'APKINDEX.tar.gz_': No such file or directory\n")

    repo = pathlib.Path(tempfile.mkdtemp(prefix="porthole-sl-repo-"))
    (repo / ".run").mkdir()
    (repo / ".run" / "pkg-status.json").write_text(json.dumps(
        {"state": "done", "rung": "pkg:webkit2gtk-6.0", "pid": 1,
         "last_at": long_ago, "started": long_ago - 3000}))
    (repo / ".run" / "build-status.json").write_text(json.dumps(
        {"state": "failed", "rung": "fast", "pid": 1, "elapsed": 30.3,
         "last_at": now - 5, "started": now - 35}))

    saved = os.environ.get("PORTHOLE_SANDBOX_PMB_DIR")
    os.environ["PORTHOLE_SANDBOX_PMB_DIR"] = str(work)
    try:
        snap, _ = sl.build_snapshot(repo, now)
    finally:
        if saved is None:
            os.environ.pop("PORTHOLE_SANDBOX_PMB_DIR", None)
        else:
            os.environ["PORTHOLE_SANDBOX_PMB_DIR"] = saved

    assert snap is not None
    assert "webkit" not in (snap.get("rung") or ""), snap
    # ...and what is left is the build the reader just ran, which is what they
    # were looking for on that line in the first place.
    assert snap["rung"] == "fast", snap


def test_a_quiet_log_is_not_a_running_build():
    """The buildroot's APKBUILD outlives the build that staged it, so naming
    alone must never put a row on screen. Liveness is the log's mtime."""
    import time
    work = pathlib.Path(tempfile.mkdtemp(prefix="porthole-sl-"))
    build = work / "chroot_buildroot_aarch64" / "home" / "pmos" / "build"
    build.mkdir(parents=True)
    (build / "APKBUILD").write_text("pkgname=webkit2gtk-6.0\n")
    (work / "log.txt").write_text("[7947/9429] Building CXX object x.o\n")
    old = time.time() - 4000
    os.utime(work / "log.txt", (old, old))

    repo = pathlib.Path(tempfile.mkdtemp(prefix="porthole-sl-repo-"))
    (repo / ".run").mkdir()
    (repo / ".run" / "pkg-status.json").write_text(json.dumps(
        {"state": "done", "rung": "pkg:x", "last_at": old, "pid": 1}))

    saved = os.environ.get("PORTHOLE_SANDBOX_PMB_DIR")
    os.environ["PORTHOLE_SANDBOX_PMB_DIR"] = str(work)
    try:
        snap, _ = sl.build_snapshot(repo, time.time())
    finally:
        if saved is None:
            os.environ.pop("PORTHOLE_SANDBOX_PMB_DIR", None)
        else:
            os.environ["PORTHOLE_SANDBOX_PMB_DIR"] = saved
    assert snap is None, f"a log quiet for an hour was read as a live build: {snap}"


# ------------------------------------- can you tell it is still alive? --

def test_a_running_row_carries_evidence_that_it_is_moving():
    """The row was `image  [ unknown ]  --` and nothing else.

    A kernel rung has no percentage -- there is no total to be a fraction of
    -- so the bar renders `unknown` and the figure renders `--`, and that is
    byte for byte what the row showed for a build in its first second, in its
    tenth minute, and one that had quietly stopped saying anything. Meanwhile
    `porthole build status`, reading the SAME snapshot, printed the phase, the
    elapsed and the last line.
    """
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(pathlib.Path(tmp))
        now = 1000.0
        _write(repo, {"rung": "image", "phase": "package", "state": "running",
                      "pid": os.getpid(), "elapsed": 659.7, "progress": None,
                      "eta": None, "compile_lines": 3805,
                      "last": "  AS  x.o", "last_at": now - 2})
        with _only_these_logs(pathlib.Path(tmp) / "nope"):
            row = sl.build_line(repo, 100, now)
        assert row, "no row at all"
        assert "10m59s" in row, row              # elapsed
        assert "package" in row, row             # phase
        assert "3805 lines" in row, row          # a number that ticks up


def test_a_long_silence_is_said_out_loud():
    """The field the reader actually wants: not "is there a build" but "has it
    said anything lately". `porthole build status` has had it (as `(Nm ago)`
    on the last line) since the stall notes were written."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(pathlib.Path(tmp))
        now = 1000.0
        _write(repo, {"rung": "image", "phase": "package", "state": "running",
                      "pid": os.getpid(), "elapsed": 900.0, "progress": None,
                      "eta": None, "compile_lines": 10,
                      "last": "  AS  x.o", "last_at": now - 400})
        with _only_these_logs(pathlib.Path(tmp) / "nope"):
            row = sl.build_line(repo, 100, now)
        assert row and "quiet" in row, row
        assert "6m40s" in row, row


def test_a_fresh_build_says_nothing_about_being_quiet():
    """The positive control. A marker that is always on is not a marker."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(pathlib.Path(tmp))
        now = 1000.0
        _write(repo, {"rung": "image", "phase": "package", "state": "running",
                      "pid": os.getpid(), "elapsed": 900.0, "progress": None,
                      "eta": None, "compile_lines": 10,
                      "last": "  AS  x.o", "last_at": now - 3})
        with _only_these_logs(pathlib.Path(tmp) / "nope"):
            row = sl.build_line(repo, 100, now)
        assert row and "quiet" not in row, row


def test_a_stale_run_does_not_get_a_moving_glyph():
    """`stale` is exactly the case where the file still says `running` --
    nothing wrote a final state because the process was killed -- so handing
    the raw snapshot to `spinner` got a spinner frame back, and the row read
    `. image  stale`: a moving glyph beside the word for not moving."""
    import porthole_progress as pp

    now = 1000.0
    snap = {"rung": "image", "phase": "package", "state": "running",
            "pid": 999999, "elapsed": 900.0, "progress": None,
            "last": "x", "last_at": now - 5}
    text = "".join(t for t, _ in sl.row_segments(snap, False, 100, now))
    assert "stale" in text, text
    frames = pp._SPIN + pp._SPIN_ASCII
    assert not any(f in text for f in frames), text


def test_a_finished_pmbootstrap_invocation_is_not_a_running_build():
    """The row read

        device-google-taimen  [ unknown ]  --  42m50s  build  · reattached

    where the name came from a staged APKBUILD an unrelated build had left
    behind 42 minutes earlier, and the freshness came from a `ccache -s` five
    seconds before. The log is shared by the WHOLE workspace, so its mtime
    says only that some pmbootstrap ran -- `status`, `index`, a `chroot --
    ccache -s`, anything. A running build has not printed DONE!.
    """
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        (base / "repo").mkdir()
        repo = _fake_repo(base / "repo")
        pmb = base / "pmb"
        staged = pmb / "chroot_buildroot_aarch64" / "home" / "pmos" / "build"
        staged.mkdir(parents=True)
        (staged / "APKBUILD").write_text("pkgname=device-google-taimen\n")
        now = time.time()
        os.utime(staged / "APKBUILD", (now - 2570, now - 2570))

        (pmb / "log.txt").write_text(
            "(1) [18:45] (native) % su pmos -c 'ccache -s'\n"
            "(1) [18:45] NOTE: chroot is still active\n"
            "(1) [18:45] DONE!\n")
        os.utime(pmb / "log.txt", (now - 3, now - 3))
        with _only_these_logs(pmb):
            assert sl.build_line(repo, 100, now) is None

        # THE POSITIVE CONTROL. The same stale staged name and the same fresh
        # mtime, with a log that is mid-compile, must still draw the row --
        # otherwise this fix is indistinguishable from deleting the feature.
        (pmb / "log.txt").write_text(
            "(1) [18:45]   CC      drivers/gpu/msm.o\n"
            "(1) [18:45]   AR      built-in.a\n")
        os.utime(pmb / "log.txt", (now - 3, now - 3))
        with _only_these_logs(pmb):
            row = sl.build_line(repo, 100, now)
        assert row and "device-google-taimen" in row, row


if __name__ == "__main__":
    sys.exit(main())
