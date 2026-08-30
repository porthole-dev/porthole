#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Two devices must never see each other's files.

The bug this locks down: PORTHOLE_WORKDIR was a single global key in a tool
whose whole model is "many devices, one at a time". Switching devices left the
previous device's repository in place, so

  - `porthole docs new` wrote one device's handoff into another's repo,
  - the milestone probes read one device's .dts files and reported them as
    evidence about another,
  - `porthole cd` sent you, and anything scripted after it, to the wrong tree.

None of that is visible when it goes wrong. It just quietly produces work in the
wrong place, and evidence read from the wrong tree is worse than no evidence.
So every rule gets a test rather than a comment.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLI = ROOT / "bin" / "porthole"
sys.path.insert(0, str(ROOT / "lib"))

import porthole  # noqa: E402


def run(*args, env=None, xdg=None):
    base = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
            "PORTHOLE_ROOT": str(ROOT), "NO_COLOR": "1",
            "XDG_CONFIG_HOME": xdg or TMPXDG}
    base.update(env or {})
    p = subprocess.run([sys.executable, str(CLI), *args],
                       capture_output=True, text=True, env=base)
    return p.returncode, p.stdout, p.stderr


class Skip(Exception):
    """This test could not run here.

    Reported as `skip`, never as a pass. A CI runner has no pmaports checkout,
    and a test that silently succeeds because its subject was absent is a test
    that will keep succeeding after the subject breaks.
    """


def needs_pmaports():
    import porthole
    import porthole_pmaports as pmap
    if not pmap.find_pmaports(porthole.load_config(root=ROOT)):
        raise Skip("no pmaports checkout on this host")


TMPXDG = tempfile.mkdtemp(prefix="porthole-iso-")


def cfg_for(device, extra=None):
    """Resolve config as the CLI would, for one device."""
    env = {"PORTHOLE_DEVICE": device}
    env.update(extra or {})
    return porthole.load_config(root=ROOT, env=env)


def with_config(text):
    """A throwaway XDG config dir holding this config.env."""
    d = tempfile.mkdtemp(prefix="porthole-iso-cfg-")
    (pathlib.Path(d) / "porthole").mkdir(parents=True, exist_ok=True)
    (pathlib.Path(d) / "porthole" / "config.env").write_text(text)
    return d


# Scratch profiles that test_cli_rules.py creates under `profiles/` and
# deletes again. The suites run in PARALLEL (tests/run-suites.sh, xargs -P),
# so a snapshot taken here can name a profile that is gone by the time the
# loops below use it -- which is exactly what happened: `aports worktree -d
# zzz-ruletest2` failed in a run where nothing was wrong with the code.
# These loops assert a property of real device profiles; a fixture that
# appears and vanishes is not one.
SCRATCH = "zzz-"

DEVICES = [d for d in porthole.list_profiles(ROOT) if not d.startswith(SCRATCH)]
A, B = (DEVICES + ["google-taimen", "google-cheetah"])[:2]


# ----------------------------------------------------------------- workdir --

def test_a_per_device_workdir_beats_the_global_key():
    xdg = with_config(f"PORTHOLE_DEVICE={A}\n"
                      f"PORTHOLE_WORKDIR=/tmp/global-wrong\n"
                      f"PORTHOLE_WORKDIR_{A.upper().replace('-', '_')}=/tmp/right-a\n")
    rc, out, err = run("config", "PORTHOLE_WORKDIR", xdg=xdg)
    assert rc == 0, err
    assert out.strip() == "/tmp/right-a", out


def test_switching_devices_never_inherits_the_other_repo():
    """The reported bug, exactly: `use B` after `use A` handed back A's path."""
    xdg = with_config(
        f"PORTHOLE_DEVICE={A}\n"
        f"PORTHOLE_WORKDIR_{A.upper().replace('-', '_')}=/tmp/only-a\n")
    rc, out, err = run("-d", B, "config", "PORTHOLE_WORKDIR", xdg=xdg)
    assert rc == 0, err
    assert out.strip() != "/tmp/only-a", (
        f"{B} inherited {A}'s working repo — the contamination is back")
    assert out.strip() == "", f"expected no workdir for {B}, got {out!r}"


def test_the_bare_global_cannot_answer_for_a_device_in_a_multi_device_setup():
    """Keeping the global as a "fallback" left the bug in place: `porthole cd`
    still resolved to whichever device wrote it last."""
    xdg = with_config(
        f"PORTHOLE_DEVICE={B}\n"
        f"PORTHOLE_WORKDIR=/tmp/belongs-to-whoever-set-it\n"
        f"PORTHOLE_WORKDIR_{A.upper().replace('-', '_')}=/tmp/only-a\n")
    rc, out, _ = run("config", "PORTHOLE_WORKDIR", xdg=xdg)
    assert out.strip() == "", (
        f"{B} got the global workdir {out.strip()!r}; in a multi-device setup "
        f"the bare key belongs to no device in particular")


def test_a_single_device_setup_still_uses_the_global_key():
    """Nothing may break for someone with one device and no per-device keys."""
    xdg = with_config(f"PORTHOLE_DEVICE={A}\nPORTHOLE_WORKDIR=/tmp/single\n")
    rc, out, _ = run("config", "PORTHOLE_WORKDIR", xdg=xdg)
    assert out.strip() == "/tmp/single", out


def test_an_environment_override_still_wins():
    """A one-off `PORTHOLE_WORKDIR=... porthole next` must keep working."""
    xdg = with_config(
        f"PORTHOLE_DEVICE={A}\n"
        f"PORTHOLE_WORKDIR_{A.upper().replace('-', '_')}=/tmp/only-a\n")
    rc, out, _ = run("config", "PORTHOLE_WORKDIR",
                     env={"PORTHOLE_WORKDIR": "/tmp/oneoff"}, xdg=xdg)
    assert out.strip() == "/tmp/oneoff", out


def test_use_reports_a_missing_workdir_rather_than_inheriting_one():
    xdg = with_config(
        f"PORTHOLE_DEVICE={A}\n"
        f"PORTHOLE_WORKDIR_{A.upper().replace('-', '_')}=/tmp/only-a\n")
    rc, out, err = run("use", B, "--json", xdg=xdg)
    assert rc == 0, err
    payload = json.loads(out)
    assert payload["workdir"] == "", payload
    assert any("no working repo" in w for w in payload["warnings"]), payload


# ---------------------------------------------------------------- pmaports --

def test_a_per_device_pmaports_beats_the_shared_clone():
    with tempfile.TemporaryDirectory() as tmp:
        fake = pathlib.Path(tmp) / "wt"
        (fake / "device").mkdir(parents=True)
        xdg = with_config(
            f"PORTHOLE_DEVICE={A}\n"
            f"PORTHOLE_PMAPORTS_{A.upper().replace('-', '_')}={fake}\n")
        rc, out, _ = run("config", "PORTHOLE_PMAPORTS", xdg=xdg)
        assert out.strip() == str(fake), out


def test_one_devices_pmaports_is_not_anothers():
    with tempfile.TemporaryDirectory() as tmp:
        fake = pathlib.Path(tmp) / "wt"
        (fake / "device").mkdir(parents=True)
        xdg = with_config(
            f"PORTHOLE_DEVICE={A}\n"
            f"PORTHOLE_PMAPORTS_{A.upper().replace('-', '_')}={fake}\n")
        rc, out, _ = run("-d", B, "config", "PORTHOLE_PMAPORTS", xdg=xdg)
        assert out.strip() != str(fake), (
            f"{B} resolved to {A}'s pmaports worktree")


def test_worktree_previews_or_refuses_but_never_writes_without_yes():
    """Two correct outcomes and no third: it previews, or it refuses because
    one already exists. What it must never do is create one -- a preview that
    modifies the user's pmbootstrap clone is not a preview."""
    needs_pmaports()
    saw_preview = saw_refusal = False
    for device in DEVICES:
        rc, out, err = run("aports", "worktree", "-d", device)
        if rc == 0:
            assert "would give" in out.lower(), out
            assert "now has its own" not in out, "it created one on a preview"
            saw_preview = True
        else:
            assert "already exists" in (out + err), (out + err)
            saw_refusal = True
    assert saw_preview or saw_refusal, "no device exercised either path"


def test_worktree_refuses_to_clobber_an_existing_one():
    """Criterion 7. Recreating a worktree silently would throw away whatever
    was uncommitted in it."""
    needs_pmaports()
    import porthole_cmd_use as use
    cfg_text = (ROOT / "profiles").parent  # noqa: F841 - readability only
    for device in DEVICES:
        key = f"PORTHOLE_PMAPORTS_{device.upper().replace('-', '_')}"
        cfg = cfg_for(device)
        if not cfg.get(key):
            continue
        rc, out, err = run("aports", "worktree", "-d", device, "--yes")
        assert rc != 0, f"{device}: recreated an existing worktree without --force"
        assert "already exists" in (out + err), (out + err)
        return
    print("      (no device has a worktree; nothing to clobber)")


# ----------------------------------------------------- downstream consumers --

def test_docs_new_writes_into_the_active_devices_repo_only():
    """The consumer that made this bug expensive: a handoff for one device
    landing in another device's repository."""
    import porthole_cmd_docs as docs

    with tempfile.TemporaryDirectory() as tmp:
        mine = pathlib.Path(tmp) / "mine"
        theirs = pathlib.Path(tmp) / "theirs"
        for d in (mine, theirs):
            d.mkdir()

        class Ctx:
            root = ROOT

            def __init__(self, workdir):
                self.cfg = {"PORTHOLE_WORKDIR": str(workdir),
                            "PORTHOLE_DEVICE": A}
                self.cfg = type("C", (), {
                    "get": lambda s, k, d="": {"PORTHOLE_WORKDIR": str(workdir),
                                               "PORTHOLE_DEVICE": A}.get(k, d)})()

        assert docs._docs_root(Ctx(mine)) == mine / "docs"
        assert docs._docs_root(Ctx(theirs)) == theirs / "docs"
        assert docs._docs_root(Ctx(mine)) != theirs / "docs"


def test_milestone_probes_read_only_the_active_workdir():
    import porthole_milestones as ms

    with tempfile.TemporaryDirectory() as tmp:
        mine = pathlib.Path(tmp) / "mine"
        theirs = pathlib.Path(tmp) / "theirs"
        mine.mkdir()
        theirs.mkdir()
        # A device tree that belongs to the OTHER device.
        (theirs / "other.dts").write_text("/dts-v1/;\n")

        class Cfg:
            def __init__(self, wd):
                self.v = {"PORTHOLE_WORKDIR": str(wd), "PORTHOLE_DTB": "other"}

            def get(self, k, d=""):
                return self.v.get(k, d)

        class Ctx:
            root = ROOT

            def __init__(self, wd):
                self.cfg = Cfg(wd)

        assert ms.probe_dts_exists(Ctx(theirs)).state == ms.DONE
        got = ms.probe_dts_exists(Ctx(mine))
        assert got.state == ms.TODO, (
            f"a probe found another device's .dts: {got.evidence}")


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = skipped = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except Skip as exc:
            skipped += 1
            print(f"  skip {name}: {exc}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERR  {name}: {type(exc).__name__}: {exc}")
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{len(tests) - failed - skipped}/{len(tests)} passed{tail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
