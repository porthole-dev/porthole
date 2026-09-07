#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""`porthole init`: the questions it asks, and the keys it writes.

The gap this file locks down. `init` set an identity, a build tier and
pmaports, and then every build verb answered "PORTHOLE_WORKDIR is not set in
the profile" -- true, and misleading twice over: it is not a profile key, and
`init`, whose entire job is to set a host up, never asked for it. A machine
that had just been "set up in one command" could not run `porthole build`,
`porthole verify`, `porthole dts` or six of the milestones `porthole next`
reports, with nothing on the screen connecting the two facts.

The second half is the KEY it writes. A working repo belongs to one device;
the bare `PORTHOLE_WORKDIR` is ignored outright the moment a second device
declares its own (lib/porthole.py), so `--workdir` writing the bare key meant
the value was silently unused on any two-device host -- the worst of the three
possible outcomes.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLI = ROOT / "bin" / "porthole"
sys.path.insert(0, str(ROOT / "lib"))

import porthole_cmd_init as init  # noqa: E402
import porthole_cli  # noqa: E402

from _runner import run as run_tests  # noqa: E402

DEV = "google-taimen"
KEY = "PORTHOLE_WORKDIR_GOOGLE_TAIMEN"


# A HOME of its own, not the tester's and not the harness's.
#
# `tests/ci-local.sh` runs the suite from a `git archive` extraction with
# HOME set INSIDE that tree, and with no .git the secrets scanner and the
# file-cleanliness check fall back to walking it -- so porthole's own
# `$HOME/.cache/porthole/registry.json`, written by any CLI call here, was
# picked up as a tracked file and failed two unrelated suites. A test that
# writes into the tree it is scanned from is a test that breaks its
# neighbours.
_HOME = tempfile.mkdtemp(prefix="porthole-init-home-")


def cli(*args, env=None, xdg=None):
    base = {"PATH": os.environ["PATH"], "HOME": _HOME,
            "PORTHOLE_ROOT": str(ROOT), "NO_COLOR": "1",
            "XDG_CONFIG_HOME": xdg or tempfile.mkdtemp(prefix="porthole-init-")}
    base.update(env or {})
    # stdin closed, because that is what tells `init` nobody is there to
    # answer: `interactive` is `sys.stdin.isatty()`, and a test inheriting the
    # runner's tty would take the interactive path and hang.
    p = subprocess.run([sys.executable, str(CLI), *args],
                       capture_output=True, text=True, env=base,
                       stdin=subprocess.DEVNULL)
    return p.returncode, p.stdout, p.stderr


class Recorder:
    """An `Out` that keeps its lines instead of printing them."""

    def __init__(self):
        self.lines = []
        self._out = porthole_cli.Out(force_colour=False)

    def __getattr__(self, name):
        return getattr(self._out, name)

    def __call__(self, *parts):
        self.lines.append(" ".join(str(p) for p in parts))

    def blank(self):
        self.lines.append("")

    def heading(self, text):
        self.lines.append(text)

    def kv(self, key, value, width=0, note=""):
        self.lines.append(f"{key} {value} {note}".strip())

    def hint(self, text):
        self.lines.append(text)

    def warn(self, text):
        self.lines.append("warning: " + text)

    def text(self):
        return "\n".join(self.lines)


class FakePrompt(init.Prompt):
    """A Prompt that answers from a script rather than from a terminal."""

    def __init__(self, out, answers):
        super().__init__(out, interactive=True)
        self.answers = list(answers)

    def _read(self, label, default):
        # Empty means "took the default" -- exactly what Prompt._read does
        # with a bare Enter, and the reply most of these tests are about.
        got = self.answers.pop(0) if self.answers else default
        return got or default


class Ctx:
    def __init__(self, out, root=ROOT, cfg=None):
        self.out, self.root, self.cfg = out, pathlib.Path(root), cfg or {}


# ------------------------------------------------------------- candidates --

def test_the_short_name_is_what_people_call_the_directory():
    """Nobody names the folder `google-taimen`. Searching for the codename
    alone found nothing on the one layout this tool is developed against."""
    assert init._short_name("google-taimen") == "taimen"
    assert init._short_name("oneplus-enchilada") == "enchilada"
    assert init._short_name("pine64-pinephone") == "pinephone"
    # No vendor prefix at all is legal and must not raise.
    assert init._short_name("solo") == "solo"


def test_a_repo_beside_the_porthole_checkout_is_found():
    """porthole, pmaports and the device repo side by side is the layout every
    doc in this tree assumes, and the one the reference host uses."""
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        (base / "porthole").mkdir()
        (base / "taimen").mkdir()
        (base / "unrelated").mkdir()
        home = base / "home"
        home.mkdir()
        found = init._workdir_candidates(base / "porthole", DEV, home)
        assert found == [base / "taimen"], found


def test_pmaports_is_never_offered_as_a_working_repo():
    """Writing it would make `porthole docs new` put this device's notes into
    a tree shared with every other device -- the contamination the per-device
    key exists to stop."""
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        (base / "porthole").mkdir()
        fake = base / "taimen"
        (fake / "device").mkdir(parents=True)
        (fake / "deviceinfo_schema.toml").write_text("")
        home = base / "home"
        home.mkdir()
        assert init._workdir_candidates(base / "porthole", DEV, home) == []


def test_the_porthole_checkout_is_never_offered_as_a_working_repo():
    """A checkout that happens to be named after the device is still the
    toolkit, and the toolkit is not anybody's device repo."""
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        root = base / "taimen"
        root.mkdir()
        home = base / "home"
        home.mkdir()
        assert init._workdir_candidates(root, DEV, home) == []


def test_a_repo_under_a_common_home_directory_is_found():
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        (base / "porthole").mkdir()
        home = base / "home"
        (home / "src" / "taimen").mkdir(parents=True)
        found = init._workdir_candidates(base / "porthole", DEV, home)
        assert found == [home / "src" / "taimen"], found


# ------------------------------------------------------------- the choice --

def test_the_working_repo_is_written_under_the_per_device_key():
    """The bare PORTHOLE_WORKDIR is ignored the moment a second device
    declares its own, so writing it is worse than writing nothing: the value
    is there, it looks right, and it is not used."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = pathlib.Path(tmp) / "taimen"
        repo.mkdir()
        out = Recorder()
        args = argparse.Namespace(workdir=str(repo))
        key, value = init._choose_workdir(
            Ctx(out), args, FakePrompt(out, []), {}, DEV)
        assert key == KEY, key
        assert value == str(repo.resolve()), value


def test_a_workdir_flag_naming_nothing_is_refused_rather_than_written():
    """Same gate `--pmaports` gets: a path written now becomes a puzzle
    later, in a message about a device that cannot be found."""
    out = Recorder()
    args = argparse.Namespace(workdir="/nonexistent/definitely/not/here")
    try:
        init._choose_workdir(Ctx(out), args, FakePrompt(out, []), {}, DEV)
    except Exception as exc:  # porthole_cli.Bail
        assert "does not exist" in str(exc), exc
    else:
        raise AssertionError("a missing --workdir path was accepted")


def test_a_configured_repo_that_exists_is_adopted_without_a_rewrite():
    """A second run must be a no-op. Rewriting a key to the value it already
    has is what makes `changed` meaningless."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Recorder()
        args = argparse.Namespace(workdir=None)
        key, value = init._choose_workdir(
            Ctx(out), args, FakePrompt(out, []), {KEY: tmp}, DEV)
        assert (key, value) == ("", ""), (key, value)


def test_the_headless_path_prints_nothing_and_never_creates_a_directory():
    """Every step runs BEFORE ctx.emit, so anything printed here lands in
    front of `--json`. And a directory created with nobody watching is a
    side effect nobody asked for."""
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        (base / "porthole").mkdir()
        home = base / "home"
        home.mkdir()
        out = Recorder()
        prompt = init.Prompt(out, interactive=False)
        args = argparse.Namespace(workdir=None)
        key, _ = init._choose_workdir(
            Ctx(out, root=base / "porthole"), args, prompt, {}, DEV, home=home)
        assert out.text() == "", out.text()
        assert key == "", key
        assert not (base / "taimen").exists(), "it created a directory headless"


def test_headless_adopts_exactly_one_candidate_and_refuses_to_pick_between_two():
    """Silently choosing between two is how a half-finished port gets written
    into the wrong repository -- the same bar `_autoselect_tree` holds."""
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        (base / "porthole").mkdir()
        (base / "taimen").mkdir()
        home = base / "home"
        home.mkdir()
        out = Recorder()
        args = argparse.Namespace(workdir=None)
        key, value = init._choose_workdir(
            Ctx(out, root=base / "porthole"),
            args, init.Prompt(out, interactive=False), {}, DEV, home=home)
        assert key == KEY and value == str((base / "taimen").resolve())

        (home / "taimen").mkdir()
        key, _ = init._choose_workdir(
            Ctx(out, root=base / "porthole"),
            args, init.Prompt(out, interactive=False), {}, DEV, home=home)
        assert key == "", "it picked between two candidates with nobody watching"


def test_creating_one_is_offered_only_when_it_is_not_already_on_the_list():
    """"create one" pointing at a directory offered two rows above it is the
    same answer twice wearing two numbers."""
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        (base / "porthole").mkdir()
        (base / "taimen").mkdir()
        home = base / "home"
        home.mkdir()
        out = Recorder()
        # Answer "1", the found candidate; the menu is what is under test.
        init._choose_workdir(Ctx(out, root=base / "porthole"),
                             argparse.Namespace(workdir=None),
                             FakePrompt(out, ["1"]), {}, DEV, home=home)
        assert out.text().count("create one") == 0, out.text()


# ------------------------------------------------------------------ menus --

def test_choose_returns_the_key_not_the_number():
    """Two menus indexed their own answers and disagreed about what `3` meant.
    Returning the key removes the arithmetic rather than duplicating it."""
    out = Recorder()
    prompt = FakePrompt(out, ["2"])
    got = prompt.choose([("a", "first", ""), ("b", "second", ""),
                         ("c", "third", "")])
    assert got == "b", got


def test_a_menu_is_never_printed_to_something_that_cannot_answer_it():
    out = Recorder()
    prompt = init.Prompt(out, interactive=False)
    got = prompt.choose([("a", "first", ""), ("b", "second", "")],
                        default="2")
    assert got == "2", got
    assert out.text() == "", out.text()


def test_an_unrecognised_reply_comes_back_as_typed():
    """The device menu treats a typed codename as an answer; swallowing it
    into option 1 would silently select the wrong phone."""
    out = Recorder()
    prompt = FakePrompt(out, ["google-cheetah"])
    got = prompt.choose([("google-taimen", "google-taimen", "")])
    assert got == "google-cheetah", got


# --------------------------------------------------------------- address --

def test_the_address_question_explains_both_routes():
    """`device IP [172.16.42.1]` is a correct number and unrecognisable as
    anything but a hardcoded guess to somebody who has not read the
    postmarketOS wiki -- so the one question a new developer could not answer
    from the screen was the one the screen asked most confidently."""
    out = Recorder()
    prompt = FakePrompt(out, [""])
    args = argparse.Namespace(host=None)
    got = init._choose_host(Ctx(out), args, prompt, {})
    assert got == init.GADGET_IP, got
    text = out.text().lower()
    assert "over usb" in text and "over wifi" in text, out.text()
    assert "usb link" in text, "the probe result was not reported"


def test_a_flag_or_a_pipe_asks_nothing_about_the_address():
    out = Recorder()
    got = init._choose_host(Ctx(out), argparse.Namespace(host="10.0.0.9"),
                            init.Prompt(out, interactive=False), {})
    assert got == "10.0.0.9", got
    assert out.text() == "", out.text()

    out = Recorder()
    got = init._choose_host(Ctx(out), argparse.Namespace(host=None),
                            init.Prompt(out, interactive=False),
                            {"PORTHOLE_HOST": "10.0.0.8"})
    assert got == "10.0.0.8", got
    assert out.text() == "", out.text()


# ------------------------------------------------------------ end to end --

def test_a_headless_run_writes_the_per_device_working_repo():
    """The whole point, through the real CLI."""
    xdg = tempfile.mkdtemp(prefix="porthole-init-e2e-")
    with tempfile.TemporaryDirectory() as tmp:
        repo = pathlib.Path(tmp) / "taimen"
        repo.mkdir()
        rc, out, err = cli("init", DEV, "--non-interactive", "--yes",
                           "--workdir", str(repo), "--json", xdg=xdg)
        assert rc == 0, err
        payload = json.loads(out)
        assert payload["workdir"] == str(repo), payload
        assert KEY in payload["keys"], payload["keys"]
        assert "PORTHOLE_WORKDIR" not in payload["keys"], (
            "the bare key is ignored in a multi-device setup and must not be "
            "written")
        text = (pathlib.Path(xdg) / "porthole" / "config.env").read_text()
        assert f"{KEY}={repo}" in text, text


def test_a_second_headless_run_changes_nothing():
    """`init` promises re-running it is safe. A key rewritten to the value it
    already holds makes `changed` meaningless."""
    xdg = tempfile.mkdtemp(prefix="porthole-init-twice-")
    with tempfile.TemporaryDirectory() as tmp:
        repo = pathlib.Path(tmp) / "taimen"
        repo.mkdir()
        for _ in range(2):
            rc, out, err = cli("init", DEV, "--non-interactive", "--force",
                               "--yes", "--workdir", str(repo), "--json",
                               xdg=xdg)
            assert rc == 0, err
        payload = json.loads(out)
        verdicts = {k: v["verdict"] for k, v in payload["keys"].items()}
        assert set(verdicts.values()) == {"kept"}, verdicts


def test_json_stays_parseable_with_every_step_in_play():
    """Every step runs before `ctx.emit`, so a step that prints breaks the
    contract `--json` makes."""
    xdg = tempfile.mkdtemp(prefix="porthole-init-json-")
    rc, out, err = cli("init", DEV, "--non-interactive", "--json", xdg=xdg)
    assert rc == 0, err
    json.loads(out)


def test_a_headless_run_previews_before_it_writes():
    """Every other verb that writes outside its own profile previews first and
    acts on --yes: `porthole pkg fork` prints the package, the source and the
    destination, then names the command that does it. init was the one
    exception, and it is the command an agent runs before it knows anything.

    The interactive path keeps no --yes: six answered questions ARE the
    confirmation, and demanding a flag after them would be hostile."""
    xdg = tempfile.mkdtemp(prefix="porthole-init-preview-")
    config = pathlib.Path(xdg) / "porthole" / "config.env"
    rc, out, err = cli("init", DEV, "--user", "u", "--host", "10.0.0.1",
                       "--json", xdg=xdg)
    assert rc == 0, err
    payload = json.loads(out)
    assert payload["would_write"] is True, payload
    assert not config.exists(), "a preview must not write the file"

    rc, out, err = cli("init", DEV, "--user", "u", "--host", "10.0.0.1",
                       "--yes", "--json", xdg=xdg)
    assert rc == 0, err
    payload = json.loads(out)
    assert payload["would_write"] is False, payload
    assert config.exists(), "--yes must write it"


if __name__ == "__main__":
    sys.exit(run_tests(globals()))
