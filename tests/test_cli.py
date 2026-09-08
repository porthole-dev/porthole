#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""bin/porthole: verbs, JSON output, exit codes.

Everything here runs with no device attached. The device-touching parts of
`doctor` are exercised through PORTHOLE_DEVICE_STATE, which short-circuits the
probe -- the probe itself is the shell lib's job and is tested there.
"""
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _runner  # noqa: E402
CLI = ROOT / "bin" / "porthole"


def run(*args, env=None, stdin=""):
    """Invoke the CLI in a clean environment. env -i semantics matter: the
    developer running the tests usually has PHONE exported."""
    base = {
        "PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
        "PORTHOLE_ROOT": str(ROOT), "XDG_CONFIG_HOME": TMPXDG,
        "NO_COLOR": "1",
    }
    base.update(env or {})
    proc = subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, env=base, input=stdin)
    return proc.returncode, proc.stdout, proc.stderr


TMPXDG = tempfile.mkdtemp(prefix="porthole-cli-test-")


# -------------------------------------------------------------------- usage --

def test_no_args_prints_an_orientation_not_a_usage_dump():
    """A bare `porthole` is someone who just cloned this asking "now what".
    A usage dump is the least useful possible answer: tell them where they
    are and what to do next, and exit 0 because nothing went wrong."""
    rc, out, err = run()
    assert rc == 0, f"rc={rc}"
    assert "device" in out.lower() and "profiles" in out.lower()
    assert "porthole" in out.lower()


def test_global_flags_work_before_and_after_the_verb():
    """`porthole doctor --no-color` is what people type. Requiring a global flag
    before the verb is a papercut, and argparse's default handling silently
    undoes the global one when the subparser flag is absent."""
    for argv in (["--no-color", "devices"], ["devices", "--no-color"],
                 ["--no-colour", "devices"], ["devices", "--no-colour"]):
        rc, out, err = run(*argv)
        assert rc == 0, f"{argv} -> rc={rc} {err}"
        assert "\033[" not in out, f"{argv} still emitted colour"


def test_unknown_verb_suggests_a_near_match():
    rc, out, err = run("doctro")
    assert rc == 64, f"rc={rc}"
    assert "doctor" in err, "should suggest the nearest verb"


def test_unknown_verb_exits_64():
    rc, out, err = run("frobnicate")
    assert rc == 64, f"rc={rc}"
    assert "frobnicate" in (out + err)


def test_help_lists_every_verb():
    rc, out, _ = run("--help")
    assert rc == 0, f"rc={rc}"
    for verb in ("init", "doctor", "config", "devices", "new-device",
                 "brain", "run"):
        assert verb in out, f"{verb} missing from --help"


# ------------------------------------------------------------------- config --

def test_config_json_reports_value_and_source():
    rc, out, _ = run("config", "--json",
                     env={"PORTHOLE_DEVICE": "google-taimen"})
    assert rc == 0, f"rc={rc}"
    data = json.loads(out)
    assert data["PORTHOLE_SOC"]["value"] == "msm8998"
    assert data["PORTHOLE_SOC"]["source"] == "profile"
    assert data["PORTHOLE_SSH_PORT"]["source"] == "default"


def test_config_single_key_prints_bare_value():
    """So `PHONE=$(porthole config PHONE)` works in a script."""
    rc, out, _ = run("config", "PORTHOLE_SOC",
                     env={"PORTHOLE_DEVICE": "google-taimen"})
    assert rc == 0, f"rc={rc}"
    assert out.strip() == "msm8998", repr(out)


def test_config_exposes_the_resolved_phone_and_host():
    rc, out, _ = run("config", "--json",
                     env={"PORTHOLE_DEVICE": "google-taimen",
                          "PHONE": "olduser@172.16.42.1"})
    data = json.loads(out)
    assert data["PHONE"]["value"] == "olduser@172.16.42.1"
    assert data["HOST"]["value"] == "172.16.42.1"


def test_config_unknown_key_exits_1():
    rc, _, err = run("config", "NOPE", env={"PORTHOLE_DEVICE": "google-taimen"})
    assert rc == 1, f"rc={rc}"
    assert "NOPE" in err


def test_missing_profile_is_reported_not_crashed():
    rc, _, err = run("config", env={"PORTHOLE_DEVICE": "nosuchdevice"})
    assert rc == 1, f"rc={rc}"
    assert "nosuchdevice" in err
    assert "Traceback" not in err, "a missing profile must not surface as a traceback"


# ------------------------------------------------------------------ devices --

def test_devices_lists_taimen_and_hides_the_template():
    rc, out, _ = run("devices")
    assert rc == 0, f"rc={rc}"
    assert "google-taimen" in out
    assert "_template" not in out


def test_devices_json_is_a_list_of_objects():
    rc, out, _ = run("devices", "--json")
    data = json.loads(out)
    names = [d["codename"] for d in data]
    assert "google-taimen" in names
    entry = next(d for d in data if d["codename"] == "google-taimen")
    assert entry["name"] == "Google Pixel 2 XL"
    assert entry["soc"] == "msm8998"


def test_devices_marks_the_active_one():
    rc, out, _ = run("devices", "--json",
                     env={"PORTHOLE_DEVICE": "google-taimen"})
    data = json.loads(out)
    entry = next(d for d in data if d["codename"] == "google-taimen")
    assert entry["active"] is True


# ------------------------------------------------------------------- doctor --

def test_doctor_json_has_checks_with_verdicts():
    rc, out, _ = run("doctor", "--json",
                     env={"PORTHOLE_DEVICE": "google-taimen",
                          "PORTHOLE_DEVICE_STATE": "ABSENT"})
    data = json.loads(out)
    assert isinstance(data["checks"], list) and data["checks"]
    for check in data["checks"]:
        assert set(check) >= {"name", "status", "detail"}
        assert check["status"] in ("ok", "warn", "fail", "skip")


def test_doctor_exits_nonzero_when_something_fails():
    rc, out, _ = run("doctor", "--json",
                     env={"PORTHOLE_DEVICE": "google-taimen",
                          "PORTHOLE_DEVICE_STATE": "ABSENT",
                          "FASTBOOT": "/nonexistent/fastboot"})
    data = json.loads(out)
    assert any(c["status"] == "fail" for c in data["checks"])
    assert rc != 0, "doctor must exit non-zero when a check fails"


def test_doctor_names_a_fix_for_every_failure():
    """A check that says something is wrong without saying what to do is the
    thing doctor exists to replace."""
    rc, out, _ = run("doctor", "--json",
                     env={"PORTHOLE_DEVICE": "google-taimen",
                          "PORTHOLE_DEVICE_STATE": "ABSENT",
                          "FASTBOOT": "/nonexistent/fastboot"})
    data = json.loads(out)
    for check in data["checks"]:
        if check["status"] == "fail":
            assert check.get("fix"), f"no fix offered for {check['name']}"


def test_doctor_survives_a_host_with_no_ping_or_fastboot():
    """A minimal host may have neither. An unguarded FileNotFoundError from
    `ping` took `porthole doctor` down entirely on python:3.8-slim -- and
    doctor is the one command someone on a bare host runs first, so it has to
    be the most robust thing here, not the least.

    Only python3.8 CI caught it, because a slim container is the only
    environment tested that genuinely lacks ping."""
    empty = tempfile.mkdtemp(prefix="porthole-nobin-")
    rc, out, err = run("doctor", "--tools", "--json",
                       env={"PORTHOLE_DEVICE": "google-taimen",
                            "PATH": empty})
    assert "Traceback" not in err, f"doctor crashed:\n{err}"
    data = json.loads(out)
    assert data["checks"], "doctor must still report with no binaries at all"
    state = next(c for c in data["checks"] if c["name"] == "device: state")
    assert state["status"] in ("warn", "skip"), state


def test_doctor_tools_checks_headers():
    rc, out, _ = run("doctor", "--tools", "--json",
                     env={"PORTHOLE_DEVICE": "google-taimen"})
    data = json.loads(out)
    assert any("header" in c["name"] for c in data["checks"])


# --------------------------------------------------------------------- init --

def test_init_is_non_interactive_without_a_tty():
    """An agent must be able to bootstrap. stdin is a pipe here, so init must
    take its values from flags and never block on input()."""
    xdg = tempfile.mkdtemp(prefix="porthole-init-")
    rc, out, err = run("init", "google-taimen", "--yes",
                       "--user", "alice", "--host", "10.0.0.9",
                       env={"XDG_CONFIG_HOME": xdg})
    assert rc == 0, f"rc={rc} err={err}"
    written = pathlib.Path(xdg) / "porthole" / "config.env"
    assert written.is_file(), "init did not write config.env"
    text = written.read_text()
    assert "PORTHOLE_USER=alice" in text
    assert "PORTHOLE_HOST=10.0.0.9" in text
    assert "PORTHOLE_DEVICE=google-taimen" in text


def test_init_prints_the_sudoers_snippet_rather_than_applying_it():
    """sudoers-nopasswd must not ship in a device package, nearly every tool
    calls `sudo -n`, and -n does not prompt -- it just fails. So a fresh
    install silently disables the whole toolbox, and an agent whose harness
    refuses to type a sudo password cannot fix it. Hand it to the human."""
    xdg = tempfile.mkdtemp(prefix="porthole-init-")
    rc, out, _ = run("init", "google-taimen", "--user", "alice",
                     env={"XDG_CONFIG_HOME": xdg})
    assert "NOPASSWD" in out, "init must print the sudoers snippet"
    assert "/etc/sudoers.d/" in out


def test_init_refuses_an_unknown_device():
    xdg = tempfile.mkdtemp(prefix="porthole-init-")
    rc, _, err = run("init", "nosuchdevice",
                     env={"XDG_CONFIG_HOME": xdg})
    assert rc != 0
    assert "nosuchdevice" in err


def test_init_does_not_clobber_an_existing_config_without_force():
    xdg = tempfile.mkdtemp(prefix="porthole-init-")
    run("init", "google-taimen", "--user", "alice", "--yes",
        env={"XDG_CONFIG_HOME": xdg})
    rc, _, err = run("init", "google-taimen", "--user", "bob",
                     env={"XDG_CONFIG_HOME": xdg})
    assert rc != 0, "a second init must not silently overwrite your identity"
    assert "--force" in err
    text = (pathlib.Path(xdg) / "porthole" / "config.env").read_text()
    assert "alice" in text


def test_init_converges_instead_of_rewriting_the_file():
    """Re-running init on a half-configured host must not cost you your edits.

    This is the bug behind "setting up a second machine is overwhelming": init
    wrote the whole file from a template, so a second run either refused or
    silently dropped every key and comment it did not know about. It now
    rewrites one line per key it actually changes, through the same set_key
    `porthole use` has always used.
    """
    xdg = tempfile.mkdtemp(prefix="porthole-init-")
    run("init", "google-taimen", "--user", "alice", "--yes",
        env={"XDG_CONFIG_HOME": xdg})
    cfg = pathlib.Path(xdg) / "porthole" / "config.env"
    # A hand-added key, a comment, and a deliberate override of something init
    # autodetects -- the three things a template rewrite destroys.
    #
    # FASTBOOT is WRITTEN here rather than edited in place: a CI runner has a
    # bare PATH with no android-tools, so init never autodetected one and the
    # edit was a no-op that made this pass for the wrong reason. The point is
    # that a value already on disk survives, which does not need init to have
    # put it there.
    cfg.write_text(cfg.read_text()
                   + "\n# a note I wrote myself\nTK_MY_OWN_KEY=keepme\n"
                     "FASTBOOT=/opt/mine/fastboot\n")

    rc, out, err = run("init", "google-taimen", "--user", "bob", "--force",
                       "--yes", env={"XDG_CONFIG_HOME": xdg})
    assert rc == 0, f"rc={rc} err={err}"
    after = cfg.read_text()
    assert "MY_OWN_KEY=keepme" in after, "init dropped a hand-added key"
    assert "# a note I wrote myself" in after, "init dropped a comment"
    assert "/opt/mine/" in after, (
        "init overwrote a tool path the developer set deliberately")
    assert "PORTHOLE_USER=bob" in after, "the key that WAS asked for did not change"


def test_init_says_what_it_kept_and_what_it_changed():
    """A run that changed nothing must say so.

    On a half-configured host "it did not complain" and "it agreed with what
    was already there" look identical, and only one of them means you are set
    up. The verdict per key is the difference.
    """
    xdg = tempfile.mkdtemp(prefix="porthole-init-")
    run("init", "google-taimen", "--user", "alice", "--yes",
        env={"XDG_CONFIG_HOME": xdg})
    rc, out, _ = run("init", "google-taimen", "--user", "alice", "--force",
                     "--yes", env={"XDG_CONFIG_HOME": xdg})
    assert rc == 0
    assert "kept" in out, "a converging run must report what it kept"
    assert "nothing to change" in out, (
        "a run that changed nothing must say that in words")


def test_init_on_the_workspace_tier_writes_no_pmbootstrap_keys():
    """The sentence that costs an afternoon, enforced.

    The image carries the pmbootstrap CLI and its source pinned together, so a
    workspace host needs neither installed and neither key set. Writing one
    anyway would point a reader at a host install that no build uses -- which
    is exactly what happened on the reference host.
    """
    xdg = tempfile.mkdtemp(prefix="porthole-init-")
    rc, out, err = run("init", "google-taimen", "--user", "alice", "--yes",
                       "--tier", "workspace", env={"XDG_CONFIG_HOME": xdg})
    assert rc == 0, f"rc={rc} err={err}"
    text = (pathlib.Path(xdg) / "porthole" / "config.env").read_text()
    assert "PORTHOLE_PMBOOTSTRAP_SRC" not in text, (
        "the workspace tier must not write a host pmbootstrap checkout")
    assert "do not install it here" in out, (
        "init must say the image carries pmbootstrap")


def test_init_refuses_a_pmaports_path_that_is_not_pmaports():
    """A path with no device/ is not pmaports, and accepting it turns every
    later "device not found" into a puzzle about the wrong thing."""
    xdg = tempfile.mkdtemp(prefix="porthole-init-")
    empty = tempfile.mkdtemp(prefix="not-pmaports-")
    rc, _, err = run("init", "google-taimen", "--user", "alice",
                     "--pmaports", empty, env={"XDG_CONFIG_HOME": xdg})
    assert rc != 0, "init accepted a directory that is not pmaports"
    assert "device/" in err, err



def test_init_never_clones_unattended():
    """A network fetch nobody asked for is not a setup step.

    init can clone pmbootstrap and pmaports, and both are the right thing to
    offer a human at a terminal. Neither is the right thing for an agent or a
    CI run to discover it has done: the first version of this shipped a silent
    clone of two repositories on any headless `init --tier host`, and the way
    it surfaced was a test suite that appeared to hang.

    A fake `git` on PATH records what would have been fetched, so this asserts
    the absence of a side effect rather than trusting the code to be read.
    """
    fake = pathlib.Path(tempfile.mkdtemp(prefix="porthole-nogit-"))
    log = fake / "clone.log"
    (fake / "git").write_text(
        f'#!/bin/sh\necho "$*" >> {log}\nexit 1\n')
    (fake / "git").chmod(0o755)
    home = tempfile.mkdtemp(prefix="porthole-home-")
    xdg = tempfile.mkdtemp(prefix="porthole-init-")

    proc = subprocess.run(
        [sys.executable, str(CLI), "init", "google-taimen", "--user", "alice",
         "--tier", "host"],
        capture_output=True, text=True, input="",
        env={"PATH": f"{fake}:/usr/bin:/bin", "HOME": home,
             "XDG_CONFIG_HOME": xdg, "PORTHOLE_ROOT": str(ROOT),
             "NO_COLOR": "1"})
    assert proc.returncode == 0, proc.stderr
    assert not log.exists(), (
        f"init cloned without being asked: {log.read_text()}")



# ---------------------------------------------------------------------- run --

def test_run_refuses_to_execute_an_on_device_tool_locally():
    """An `on-device` tool executes ON the phone. Running it on the host
    produces plausible, entirely wrong output -- host load averages and host
    process names -- with nothing to signal the mistake. It must be piped to
    the device, or refused when the device is not there.

    Caught in practice: `porthole run ph-sysstate.sh` printed the workstation's
    22GB of RAM and firefox, which reads exactly like a working measurement."""
    rc, out, err = run("run", "ph-sysstate.sh",
                       env={"PORTHOLE_DEVICE": "google-taimen",
                            "PORTHOLE_DEVICE_STATE": "ABSENT"})
    assert rc == 76, f"expected 76 (wrong device state), got {rc}"
    assert "device" in (out + err).lower()
    # The giveaway that it ran locally would be host-shaped output.
    assert "firefox" not in out


def test_run_reports_an_unknown_tool_with_suggestions():
    rc, _, err = run("run", "tk-suspend",
                     env={"PORTHOLE_DEVICE": "google-taimen"})
    assert rc != 0
    assert "tk-suspend" in err


# -------------------------------------------------------------------- runner --

def test_a_tools_refusal_is_never_reported_as_its_version():
    """`porthole version` printed `unknown option -- -` as ssh's version for
    as long as the row existed: ssh has no `--version`, and the probe took
    the first line of anything it got. Then, once refusals were skipped, a
    bare `version` was read by ssh as a HOSTNAME -- a network round trip
    inside `porthole version`, answering `Pseudo-terminal will not be
    allocated`. A digit is the general test: every version has one and none
    of those excuses does."""
    sys.path.insert(0, str(ROOT / "lib"))
    import porthole_cmd_version as vv

    assert not vv._plausible("unknown option -- -")
    assert not vv._plausible("usage: ssh [-46AaCfGgKkMNnqsTtVvXxYy]")
    assert not vv._plausible(
        "Pseudo-terminal will not be allocated because stdin is not a")
    assert not vv._plausible("")
    assert vv._plausible("OpenSSH_10.2p1, OpenSSL 3.5.7 9 Jun 2026")
    assert vv._plausible("git version 2.55.0")
    # ssh is the case that motivated all of it, and it is on every port host.
    if shutil.which("ssh"):
        assert "OpenSSH" in vv.tool_version("ssh"), vv.tool_version("ssh")
    # The cache is keyed on the PROBE as well as on the binary. Without that,
    # this fix reaches nobody who has run `porthole version` before: the wrong
    # answer was keyed on a binary that has not moved in months.
    assert isinstance(vv.PROBE, int)
    src = (ROOT / "lib" / "porthole_cmd_version.py").read_text()
    assert "f\"{PROBE}:{path}" in src, "the cache key must include the probe"


def test_one_alphabet_for_how_it_went():
    """Five modules had grown their own `paint(sym("●", "*"), "green")`, and
    doctor its own ok/warn/FAIL colour table. A reader should not have to
    learn a second alphabet per verb."""
    sys.path.insert(0, str(ROOT / "lib"))
    from porthole_cli import Out

    plain = Out(stream=io.StringIO(), force_colour=False)
    fancy = Out(stream=io.StringIO(), force_colour=True)
    # A mark that is off keeps the column's width, or a list of them ceases
    # to be a column.
    assert len(plain.mark("active", False)) == len(plain.mark("active"))
    assert "\033[32m" in fancy.mark("ok")
    assert "\033[31m" in fancy.mark("fail")
    # The glyph carries the colour; the WORD only when it is a problem --
    # `ok` twice in green is emphasis spent on the rows nobody must read.
    assert "\033[90m" in fancy.status("ok")
    assert "\033[31m" in fancy.status("fail", "FAIL")
    assert "\033[33m" in fancy.status("warn")
    # Both, always: a glyph alone is unreadable in a pasted log, a word alone
    # is what made a wall of doctor rows impossible to scan.
    assert "ok" in plain.status("ok")
    assert plain.status("ok").strip()[0] in "+."


def test_a_labelled_row_dims_its_label_not_its_value():
    """Emphasis is a budget: in `elapsed  2m41s` the label is the half the
    reader already knows -- they asked for it."""
    sys.path.insert(0, str(ROOT / "lib"))
    from porthole_cli import Out

    buf = io.StringIO()
    Out(stream=buf, force_colour=True).kv("elapsed", "2m41s", 10)
    line = buf.getvalue()
    assert "\033[90melapsed" in line, line
    assert "\033[90m2m41s" not in line, line


# ---------------------------------- the environment a child process is given --

def test_a_child_environment_drops_every_stale_export():
    """#63: a dead export outlives the file it names.

    PMB_SUDO named a privilege broker porthole deleted on 2026-08-29. On the
    reference host it was still exported eleven days later -- from the systemd
    USER MANAGER, so it was in no rc file, no environment.d, and every new
    terminal and every agent inherited it. pmbootstrap invokes it directly, so
    a build died with exit 78 naming nothing.

    Generic over the table rather than hardcoding PMB_SUDO: the next entry gets
    this coverage without anyone remembering to ask for it."""
    sys.path.insert(0, str(ROOT / "lib"))
    from porthole_cli import STALE_EXPORTS, child_env

    assert STALE_EXPORTS, "the table is empty; this test now asserts nothing"
    base = dict({k: "leftover" for k in STALE_EXPORTS}, PATH="/usr/bin")
    env = child_env(base)
    for key in STALE_EXPORTS:
        assert key not in env, f"{key} reached the child: {env}"
    # THE POSITIVE CONTROL. A child_env that returned {} would pass every
    # assertion above and break every build in the repo.
    assert env.get("PATH") == "/usr/bin", "the rest of the environment must stand"


def test_every_stale_export_says_why_it_is_one():
    """A bare name in the table is a rule nobody can audit or retire. Each
    entry carries the incident, the way the permissions deny table does."""
    sys.path.insert(0, str(ROOT / "lib"))
    from porthole_cli import STALE_EXPORTS

    thin = [k for k, why in STALE_EXPORTS.items() if len(why or "") < 40]
    assert not thin, f"no reason given for: {thin}"


def test_the_resolved_config_beats_the_shell_it_was_launched_from():
    """`cfg` is what the verb resolved from the profile and the config files;
    the shell's exports are what a stale terminal happens to be carrying. The
    resolved value has to win, which is the same reason a build refuses on
    config drift -- and only PORTHOLE_*/TK_ cross, so a cfg key is never a
    route to setting arbitrary environment in a child."""
    sys.path.insert(0, str(ROOT / "lib"))
    from porthole_cli import child_env

    env = child_env({"PORTHOLE_ARCH": "stale", "PATH": "/usr/bin"},
                    {"PORTHOLE_ARCH": "aarch64", "PORTHOLE_X": "1",
                     "HOME": "/should/not/cross", "PORTHOLE_N": 5})
    assert env["PORTHOLE_ARCH"] == "aarch64", "the stale shell value won"
    assert env["PORTHOLE_X"] == "1"
    assert env.get("PATH") == "/usr/bin"
    assert env.get("HOME") != "/should/not/cross", "a non-PORTHOLE key crossed"
    assert "PORTHOLE_N" not in env, "a non-string cfg value crossed"


# 192.0.2.0/24 is TEST-NET-1 (RFC 5737): guaranteed unrouteable, so a verb
# that dials the device hangs for its full connect timeout instead of failing
# fast against a real address that happens to refuse.
BLACKHOLE = "192.0.2.1"


def test_the_host_only_verbs_stay_under_their_budget():
    """These verbs touch nothing and must not wait on the network. The test
    measures a control verb (version, ~interpreter startup only) and uses it
    as a reference point for a relative budget on the host-only verbs.

    Budget strategy:
    - Control (porthole version): 4.0 s absolute. Version touches only local
      tool versions and git metadata; 0.09–0.14 s is typical. A 4 s budget is
      ~30× the real cost (leaving headroom for scheduling jitter on a loaded
      runner) while sitting well below ~5.5 s a single ssh connect timeout costs.
      This budget catches a dial of the magnitude this test was written for
      (~5.5 s, one ConnectTimeout=5 ssh). A partial dial smaller than ~3.9 s
      would pass all tiers: the control baseline shifts with it, so every delta
      stays flat and the relative check stays blind.
    - Host-only verbs (--help, devices, doctor --no-device):
      3 s relative to the control AND 8 s absolute. The relative check catches
      regressions even on a loaded runner because both verbs and control share
      scheduling jitter equally. A network dial adds fixed ~5 s that load does
      not, so a 3 s delta cleanly separates normal (~0.6–0.8 s) from broken
      (~5.5 s). The 8 s absolute backstop is a safety ceiling.

    The unrouteable device address (192.0.2.0/24 is RFC 5737 TEST-NET-1) forces
    a dial to cost a full connect timeout, not a fast refusal -- signal is
    seconds, not milliseconds.

    Retry on budget exceedance: When a verb exceeds budget, re-measure both the
    control and that verb. Only report failure if it exceeds again on the second
    reading. This filters transient scheduling jitter in a shared test runner
    (where a contention burst can land on one verb while the pool is quiet) from
    real regressions like a network dial, which repeat consistently.

    Same HOME dependency as test_no_device_does_not_open_a_connection_to_the_
    device (tests/test_doctor.py): `doctor --no-device`'s workspace check only
    reaches ssh through `_device_key_authorized`, which returns early with no
    device key file to read. run()'s HOME defaults to the real one, so on a
    clean HOME (any CI runner) this budget measured nothing but "the CLI
    starts" -- the entire three-tier design was inert on the only surfaces
    that run it. A temp HOME with a fake key makes the budget cover the case
    that actually costs seconds when the guard regresses.
    """
    fake_home = tempfile.mkdtemp(prefix="porthole-cli-budget-home-")
    key_dir = pathlib.Path(fake_home) / ".porthole"
    key_dir.mkdir()
    (key_dir / "device_key").write_text("fake key, never read\n")

    env = {"PORTHOLE_DEVICE_STATE": "absent",
           "PORTHOLE_HOST": BLACKHOLE, "PHONE": "pmos@" + BLACKHOLE,
           "HOME": fake_home}
    # Time the control first: version touches nothing, so its time is pure
    # interpreter startup plus scheduling jitter.
    started = time.monotonic()
    run("version", env=env)
    control = time.monotonic() - started

    control_budget_s = 4.0
    relative_budget_s = 3.0
    absolute_budget_s = 8.0
    slow = []

    # Check the control itself. Same retry as every other tier below: `make
    # test` deliberately oversubscribes the runner, so a lone jitter spike
    # here must not fail the whole run -- only a repeat on a second reading
    # is a real regression. Left without this retry, the control was the one
    # tier a jitter spike could take down outright, which is the exact flake
    # the retry exists to prevent, just left open on this one tier.
    if control > control_budget_s:
        started = time.monotonic()
        run("version", env=env)
        control_retry = time.monotonic() - started
        if control_retry > control_budget_s:
            slow.append(
                f"porthole version (control): {control_retry:.2f}s (second "
                f"reading) exceeds the control budget of {control_budget_s}s "
                "-- the control itself is waiting on something, so the "
                "relative budget below it is blind")
        control = control_retry

    # Check the host-only verbs.
    for argv in (["--help"], ["devices"], ["doctor", "--no-device"]):
        started = time.monotonic()
        run(*argv, env=env)
        took = time.monotonic() - started
        if took > control + relative_budget_s or took > absolute_budget_s:
            # Budget exceeded; retry both control and verb to filter transient jitter.
            started = time.monotonic()
            run("version", env=env)
            control_retry = time.monotonic() - started
            started = time.monotonic()
            run(*argv, env=env)
            took_retry = time.monotonic() - started
            # Only fail if it repeats on the second reading.
            if took_retry > control_retry + relative_budget_s or took_retry > absolute_budget_s:
                slow.append(
                    f"porthole {' '.join(argv)}: {took_retry:.2f}s (second reading) "
                    f"(control {control_retry:.2f}s, delta {took_retry - control_retry:.2f}s)")
    assert not slow, (
        "these run with no device attached and must not wait on the network:\n  "
        + "\n  ".join(slow)
        + f"\n(control budget: {control_budget_s}s; "
        + f"relative budget: +{relative_budget_s}s; "
        + f"absolute budget: {absolute_budget_s}s; "
        + "docs/PERFORMANCE.md, 'The verbs')")


def main():
    return _runner.run(globals())


def test_an_old_tool_name_is_answered_with_its_new_one():
    """Every brain note and every agent's memory holds the old names. A bare
    'unknown tool' sends the reader looking for a file that was renamed, which
    is the most expensive possible answer to the cheapest possible question.

    No compatibility symlink: `collect()` skips symlinks, so a shim would sit
    in the tree invisible to the catalogue, to docs/TOOLS.md and to
    `porthole tools audit` -- a second copy of everything that nothing checks.
    """
    rc, out, err = run("run", "tk-fps.py")
    assert rc != 0, "it must not silently run something"
    assert "ph-fps.py" in (out + err), (out, err)

    rc, out, err = run("tools", "tk-device.sh")
    assert "ph-device.sh" in (out + err), (out, err)

    # An old name with no new twin is still just unknown -- the answer is only
    # worth giving when there is one.
    rc, out, err = run("run", "tk-not-a-real-tool.sh")
    assert rc != 0, (out, err)
    assert "ph-not-a-real-tool.sh" not in (out + err), (
        "it invented a replacement that does not exist")


# ------------------------------------------------------- confirmation tiers --

def test_a_reversible_operation_needs_no_confirmation():
    """`porthole build mod --yes` was 30 characters to push one module that a
    reboot undoes. The gate bought nothing and cost every iteration."""
    sys.path.insert(0, str(ROOT / "lib"))
    import porthole_cli as cli
    import porthole_plan as plan
    assert cli.tier(plan.op("mod")) == 1
    assert cli.gate_flag(plan.op("mod")) == ""


def test_an_irreversible_operation_needs_a_flag_that_names_the_loss():
    """--yes cannot mean both "push a module" and "erase the rootfs". The
    second flag cannot arrive by muscle memory from a different command."""
    sys.path.insert(0, str(ROOT / "lib"))
    import porthole_cli as cli
    import porthole_plan as plan
    assert cli.tier(plan.op("flash-full")) == 3
    assert cli.gate_flag(plan.op("flash-full")) == "--replace-rootfs"


def test_flashing_boot_is_gated_but_not_at_the_top_tier():
    sys.path.insert(0, str(ROOT / "lib"))
    import porthole_cli as cli
    import porthole_plan as plan
    assert cli.tier(plan.op("flash-boot")) == 2
    assert cli.gate_flag(plan.op("flash-boot")) == "--yes"


def test_the_short_binary_is_the_same_program():
    """Two entry points that can drift are two programs."""
    assert (ROOT / "bin" / "ph").read_text() == (ROOT / "bin" / "porthole").read_text()


if __name__ == "__main__":
    sys.exit(main())
