#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device (run it on the device, e.g. piped over ssh)
# env: -
# exits: 0 ok · 1 failed
"""Drive and inspect the phosh session deterministically. Run ON THE DEVICE.

Every measurement in this tree that turned out to be wrong was wrong the same
way: the benchmark ran, produced a number, and nobody checked what was actually
on the screen. A session on the lock screen, an app that exited during launch,
a page that failed to load and a gesture aimed at a widget that was already at
its end stop all report as "low frame rate" and none of them are.

So: no number without a witness. This wraps lswt (which window exists), wlrctl
(put it in front) and grim (what is actually being displayed) so a run can
assert its own preconditions and fail loudly instead of quietly measuring
nothing.

  ph-ui.py list                       toplevels, one "app-id\ttitle" per line
  ph-ui.py hash                       md5 of the current frame
  ph-ui.py settle [TIMEOUT]           wait until the screen stops changing
  ph-ui.py unblank                    undo the screensaver blank (a touch will not)
  ph-ui.py launch APP_ID CMD...       run CMD, wait for its window, focus it
  ph-ui.py focus APP_ID               raise an existing window, verify
  ph-ui.py require APP_ID             exit nonzero unless APP_ID has a window
"""
import hashlib
import re
import os
import subprocess
import sys
import time

# The session user's runtime dir. Root may open the wayland socket -- there is
# no auth on it -- but NOT the session bus, so anything on DBus below has to be
# run back as this user.
SESSION_UID = 10000
RUNTIME_DIR = "/run/user/%d" % SESSION_UID

ENV = dict(os.environ,
           XDG_RUNTIME_DIR=RUNTIME_DIR,
           WAYLAND_DISPLAY="wayland-0",
           DBUS_SESSION_BUS_ADDRESS="unix:path=%s/bus" % RUNTIME_DIR)


def run(*cmd, **kw):
    # Always bounded. grim blocks forever if the output is DPMS-off -- the
    # screen simply idled out -- and an unbounded call there hangs the whole
    # harness in a way that looks exactly like the device having crashed.
    kw.setdefault("timeout", 15)
    try:
        return subprocess.run(cmd, env=ENV, capture_output=True, **kw)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 1, b"", b"timed out (screen off?)")
    except FileNotFoundError:
        # A missing helper has to look like a failed command, not an exception:
        # every caller here already handles a non-zero return, and the one that
        # did not -- toplevels() -- took out every drag on 2026-09-09 when lswt
        # turned out not to have survived a rootfs reflash.
        return subprocess.CompletedProcess(cmd, 127, b"", b"%s: not installed"
                                           % cmd[0].encode())


def toplevels():
    """[{"app-id":..., "title":...}] from lswt, or wlrctl where lswt is absent.

    Not `lswt -j`: it emits the identifier as a bare hex token rather than a
    string, so the "JSON" does not parse. The columnar output is stable and
    has no such problem.

    lswt is not packaged by Alpine and has to be built by hand, so it does not
    survive a rootfs reflash -- and its absence took out every drag on
    2026-09-09, because a gesture arm that cannot list toplevels raises inside
    the bench and the page simply never moves. wlrctl IS packaged and answers
    the same question, one `app_id: title` per line, so fall back to it rather
    than making the whole toolbox depend on a hand-built binary.
    """
    out = run("lswt")
    if out.returncode == 0:
        rows = out.stdout.decode(errors="replace").splitlines()
        return [{"app-id": p[0].strip(), "title": p[-1].strip(), "identifier": line}
                for line in rows[1:] if line.strip()
                for p in [re.split(r"\s{2,}", line.strip(), maxsplit=1)]]
    alt = run("wlrctl", "toplevel", "list")
    if alt.returncode:
        raise SystemExit("neither lswt nor wlrctl could list toplevels: %s"
                         % (out.stderr or alt.stderr).decode().strip())
    return [{"app-id": line.split(":", 1)[0].strip(),
             "title": line.split(":", 1)[1].strip() if ":" in line else "",
             "identifier": line}
            for line in alt.stdout.decode(errors="replace").splitlines() if line.strip()]


def screen_hash():
    """md5 of the raw frame. grim costs one screencopy, which is one composite
    -- fine between measurements, never inside one."""
    out = run("grim", "-t", "ppm", "-")
    if out.returncode:
        return None
    return hashlib.md5(out.stdout).hexdigest()


def unblank():
    """Make the screen usable by an arm: on, unlocked, and showing the app.

    Three separate things, each of which has voided arms on its own -- see the
    inline comments. Returns False if the screen could not be turned on or
    unlocked; dismissing the overview is best-effort, because a session that
    was never in it must not fail here.

    phosh's screensaver powers the output down after a few minutes idle, and an
    INJECTED touch does NOT wake it -- uinput events reach the client but never
    the idle notifier. So an unattended arm drags against a dark screen: the
    page does not scroll, the DPU counts zero frames, and the only thing that
    says why is `run()`'s "screen off?" timeout, which reads like a hung device.

    Writing bl_power=0 is not the lever. Measured 2026-09-05: the backlight
    comes on, the compositor's output stays off, and the same drag still moves
    the page zero pixels. Only the screensaver's own call restores scanout --
    and only as the session user, because from root the session bus answers
    "Call failed: Socket not connected".
    """
    cmd = ["busctl", "--user", "call", "org.gnome.ScreenSaver",
           "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver",
           "SetActive", "b", "false"]
    if os.getuid() == 0:
        cmd = ["sudo", "-n", "-u", "#%d" % SESSION_UID, "env",
               "XDG_RUNTIME_DIR=" + RUNTIME_DIR,
               "DBUS_SESSION_BUS_ADDRESS=unix:path=%s/bus" % RUNTIME_DIR] + cmd
    on = run(*cmd).returncode == 0
    # Powering the output back on is only half of it: phosh keeps its LOCK
    # surface on top, so every injected touch lands on the lockscreen and the
    # app behind it never moves. That reads exactly like a dead gesture -- on
    # 2026-09-09 three arms in a row reported "the gesture hit nothing that
    # animates" with a perfectly healthy browser one surface down. loginctl
    # is the lever; the screensaver's own DBus API has no Unlock.
    unlocked = run("sudo", "-n", "loginctl", "unlock-sessions").returncode == 0
    # phosh's overview/app grid is a shell layer surface: it sits over every
    # toplevel, and activating a toplevel does NOT dismiss it. It is where the
    # session lands whenever the last window closes -- which is the FIRST thing
    # every arm here does -- so an arm that relaunches its app and starts
    # dragging is dragging at the launcher. Escape closes it; measured
    # 2026-09-09, and nothing else tried did (wlrctl activate, unblank,
    # loginctl unlock).
    key = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ph-key.py")
    if os.path.exists(key):
        run("sudo", "-n", "python3", key, "esc")
    return on and unlocked


def settle(timeout=10.0, quiet_for=0.6):
    """Wait until the screen has been identical for quiet_for seconds."""
    deadline = time.monotonic() + timeout
    last, stable_since = None, time.monotonic()
    while time.monotonic() < deadline:
        h = screen_hash()
        if h != last:
            last, stable_since = h, time.monotonic()
        elif time.monotonic() - stable_since >= quiet_for:
            return last
        time.sleep(0.15)
    return last


def find(app_id):
    return [t for t in toplevels() if t.get("app-id") == app_id]


def wait_for(app_id, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = find(app_id)
        if got:
            return got[0]
        time.sleep(0.3)
    return None


def focus(app_id):
    out = run("wlrctl", "toplevel", "focus", "app_id:" + app_id)
    if out.returncode:
        raise SystemExit("wlrctl could not focus %s: %s"
                         % (app_id, out.stderr.decode().strip()))
    settle()


def launch(app_id, cmd, timeout=90.0):
    before = {t.get("identifier") for t in toplevels()}
    # A file, not a pipe: this process exits as soon as the window is up, and
    # a child still writing to a closed pipe takes SIGPIPE and dies -- which
    # looks exactly like the app crashing on its own.
    logpath = "/tmp/tk-launch-%s.log" % app_id
    log = open(logpath, "w+b")
    # Into the user manager, not this shell. Anything started directly from an
    # ssh command lands in that ssh session's scope and is killed the moment
    # the command returns -- which reads as "the app crashed on startup" and
    # leaves the next benchmark measuring an empty shell.
    unit = "tk-app-%s" % app_id.replace(".", "-")
    subprocess.run(["systemctl", "--user", "stop", unit + ".service"],
                   env=ENV, capture_output=True)
    proc = subprocess.Popen(
        ["systemd-run", "--user", "--collect", "--unit", unit, "--"] + list(cmd),
        env=ENV, stdout=log, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True)
    top = wait_for(app_id, timeout)
    if top is None:
        # The usual cause is the process dying on startup, and its own output
        # is the only thing that says why.
        if proc.poll() is not None:
            log.seek(0)
            tail = log.read().decode(errors="replace")[-800:]
            raise SystemExit("%s exited %d before mapping a window:\n%s"
                             % (cmd[0], proc.returncode, tail))
        raise SystemExit("%s never mapped a window with app-id %r; toplevels "
                         "now: %s" % (cmd[0], app_id,
                                      [t.get("app-id") for t in toplevels()]))
    if top.get("identifier") in before:
        print("note: reusing an existing %s window" % app_id, file=sys.stderr)
    focus(app_id)
    return top


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "list":
        for t in toplevels():
            print("%s\t%s" % (t.get("app-id"), t.get("title")))
    elif cmd == "hash":
        print(screen_hash())
    elif cmd == "settle":
        print(settle(float(argv[1]) if len(argv) > 1 else 10.0))
    elif cmd == "unblank":
        print("screen on" if unblank() else "the screensaver refused")
    elif cmd == "focus":
        focus(argv[1])
        print("focused %s" % argv[1])
    elif cmd == "require":
        if not find(argv[1]):
            raise SystemExit("no window with app-id %r (have: %s)"
                             % (argv[1], [t.get("app-id") for t in toplevels()]))
        print("ok: %s is open" % argv[1])
    elif cmd == "launch":
        top = launch(argv[1], argv[2:])
        print("up: app-id=%s title=%r" % (top.get("app-id"), top.get("title")))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
