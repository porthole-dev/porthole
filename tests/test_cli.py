#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""bin/porthole: verbs, JSON output, exit codes.

Everything here runs with no device attached. The device-touching parts of
`doctor` are exercised through PORTHOLE_DEVICE_STATE, which short-circuits the
probe -- the probe itself is the shell lib's job and is tested there.
"""
import json
import os
import pathlib
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
                          "PHONE": "user@172.16.42.1"})
    data = json.loads(out)
    assert data["PHONE"]["value"] == "user@172.16.42.1"
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
    rc, out, err = run("init", "--device", "google-taimen",
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
    rc, out, _ = run("init", "--device", "google-taimen", "--user", "alice",
                     env={"XDG_CONFIG_HOME": xdg})
    assert "NOPASSWD" in out, "init must print the sudoers snippet"
    assert "/etc/sudoers.d/" in out


def test_init_refuses_an_unknown_device():
    xdg = tempfile.mkdtemp(prefix="porthole-init-")
    rc, _, err = run("init", "--device", "nosuchdevice",
                     env={"XDG_CONFIG_HOME": xdg})
    assert rc != 0
    assert "nosuchdevice" in err


def test_init_does_not_clobber_an_existing_config_without_force():
    xdg = tempfile.mkdtemp(prefix="porthole-init-")
    run("init", "--device", "google-taimen", "--user", "alice",
        env={"XDG_CONFIG_HOME": xdg})
    rc, _, err = run("init", "--device", "google-taimen", "--user", "bob",
                     env={"XDG_CONFIG_HOME": xdg})
    assert rc != 0, "a second init must not silently overwrite your identity"
    assert "--force" in err
    text = (pathlib.Path(xdg) / "porthole" / "config.env").read_text()
    assert "alice" in text


# -------------------------------------------------------------------- runner --

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
