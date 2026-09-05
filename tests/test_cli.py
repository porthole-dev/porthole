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

ROOT = pathlib.Path(__file__).resolve().parent.parent
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
    rc, out, err = run("init", "google-taimen",
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
    run("init", "google-taimen", "--user", "alice",
        env={"XDG_CONFIG_HOME": xdg})
    rc, _, err = run("init", "google-taimen", "--user", "bob",
                     env={"XDG_CONFIG_HOME": xdg})
    assert rc != 0, "a second init must not silently overwrite your identity"
    assert "--force" in err
    text = (pathlib.Path(xdg) / "porthole" / "config.env").read_text()
    assert "alice" in text


# ---------------------------------------------------------------------- run --

def test_run_refuses_to_execute_an_on_device_tool_locally():
    """An `on-device` tool executes ON the phone. Running it on the host
    produces plausible, entirely wrong output -- host load averages and host
    process names -- with nothing to signal the mistake. It must be piped to
    the device, or refused when the device is not there.

    Caught in practice: `porthole run tk-sysstate.sh` printed the workstation's
    22GB of RAM and firefox, which reads exactly like a working measurement."""
    rc, out, err = run("run", "tk-sysstate.sh",
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
                    {"PORTHOLE_ARCH": "aarch64", "TK_X": "1",
                     "HOME": "/should/not/cross", "PORTHOLE_N": 5})
    assert env["PORTHOLE_ARCH"] == "aarch64", "the stale shell value won"
    assert env["TK_X"] == "1"
    assert env.get("PATH") == "/usr/bin"
    assert env.get("HOME") != "/should/not/cross", "a non-PORTHOLE key crossed"
    assert "PORTHOLE_N" not in env, "a non-string cfg value crossed"


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
