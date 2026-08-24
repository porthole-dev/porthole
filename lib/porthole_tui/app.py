# SPDX-License-Identifier: MIT
"""The event loop, the chrome, and the safety boundary.

Curses lives here and nowhere else. Everything a pane decides is a pure
function of the snapshot, so the panes are tested against a recording fake
window and this file holds only the parts a terminal is required for.

The safety rule is enforced here rather than per pane, because a rule that each
pane has to remember is a rule one pane will forget: **nothing runs without a
confirmation unless the milestone that owns it says it is safe** -- safe meaning
read-only or trivially reversible on the host. Anything that flashes, writes to
the device, or touches a slot drops to a full-screen confirm naming the exact
command. Same boundary `porthole next` and `porthole sandbox` already draw.
"""
from __future__ import annotations

import curses
import os
import pathlib
import shlex
import subprocess
import sys

from . import theme as T
from .model import Model
from .panes import brain, console, devices, palette, port, tools

PANES = [("port", port), ("devices", devices), ("tools", tools),
         ("brain", brain), ("console", console)]

# Words that mean a command can change the device or the host irreversibly.
# This is a SECOND opinion, not the first: `safe` is a human judgement recorded
# in the milestone table, and a table can be edited in a hurry. If either the
# table or this list says stop, we stop.
DANGEROUS = ("flash", "set_active", "erase", "format", "dd ", "mkfs",
             "reboot", "fastboot", "install", "zap", "rm ")


def needs_confirmation(command: str, safe: bool) -> bool:
    """Must this be confirmed before it runs?

    A pure function on purpose. The safety boundary is the one piece of this
    app that must be testable without a terminal, because "we were careful" is
    not a mechanism and a mis-keystroke that flashes a device is not
    recoverable by pressing undo.

    Two independent reasons to stop, and either is enough:

      - the milestone did not mark the step safe (it may be destructive, or
        merely long and attention-needing -- both deserve a prompt);
      - the command text names something irreversible, whatever the table said.
    """
    if not command:
        return True
    return (not safe) or any(word in command for word in DANGEROUS)


class App:
    def __init__(self, root, device=None):
        self.root = pathlib.Path(root)
        self.model = Model(root, device)
        self.pane = 0
        self.sel = 0
        self.query = ""
        self.in_palette = False
        self.in_filter = False
        self.message = ""
        self.message_role = T.DIM
        self.items: list = []
        self.console_lines: list = []
        self.count = 0
        self.page = 10
        self.running = True

    def move(self, delta: int) -> None:
        """Move the selection, clamped to what actually exists.

        text_key used to do a bare `self.sel += 1`, so holding the down arrow
        in the palette or a filter ran the cursor past the end of the list and
        off the bottom of the screen. Clamping in ONE place is the fix: the
        alternative is remembering to bound it at four call sites, and the four
        did not agree.
        """
        self.sel = max(0, min(self.sel + delta, max(0, self.count - 1)))

    # --------------------------------------------------------------- run --

    def run(self, stdscr) -> int:
        curses.curs_set(0)
        # timeout(), not nodelay() + napms(): curses.napms holds the GIL for
        # its whole sleep, which starved the refresh thread so completely that
        # the model never finished loading and the console sat on its "reading
        # the port" frame forever. getch() with a timeout blocks on a read, so
        # the interpreter hands the GIL to the worker while it waits.
        stdscr.timeout(60)
        stdscr.keypad(True)
        T.init()
        # Paint BEFORE loading anything. The first version blocked on a full
        # model build before its first frame, so a slow probe showed the user
        # an empty terminal with no way to tell "starting" from "hung" -- and
        # one probe was walking a two-million-file kernel tree, so it hung.
        # A frame first means the app is always visibly alive, and a probe that
        # gets slow again degrades to a stale screen instead of a black one.
        try:
            self.draw(stdscr)
        except curses.error:
            pass
        self.model.refresh()

        while self.running:
            try:
                self.draw(stdscr)
            except curses.error:
                pass                      # a resize mid-draw; next frame wins
            ch = stdscr.getch()           # blocks up to 60ms, releasing the GIL
            if ch == -1:
                continue                  # timed out: redraw and wait again
            self.key(stdscr, ch)
        return 0

    # -------------------------------------------------------------- draw --

    def draw(self, stdscr) -> None:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        snap = self.model.snapshot
        self.header(stdscr, snap, w)

        if not snap.stamp and not snap.error:
            # First frame, model still loading. Say so rather than drawing an
            # empty pane that reads as a broken tool.
            stdscr.addstr(2, 2, "reading the port…", T.attr(T.DIM))
            self.footer(stdscr, w, h)
            stdscr.refresh()
            return

        body = _Sub(stdscr, 1, h - 2)
        if self.in_palette:
            if not self.items:
                self.items = palette.collect(snap)
            n = palette.render(body, snap, h - 2, w, self.sel, self.query,
                               self.items)
        else:
            name, mod = PANES[self.pane]
            if name in ("tools", "brain"):
                n = mod.render(body, snap, h - 2, w, self.sel, self.query)
            elif name == "console":
                n = mod.render(body, snap, h - 2, w, self.sel,
                               self.console_lines)
            else:
                n = mod.render(body, snap, h - 2, w, self.sel)
        self.count = n
        # A page is the visible list, less its header rows -- so PgDn moves by
        # what you can actually see rather than a guessed constant.
        self.page = max(1, h - 7)
        # A pane can shrink under the cursor (a filter narrows the list, the
        # terminal is resized). Re-clamp after every render, or the selection
        # points at a row that is no longer there.
        if self.sel > max(0, n - 1):
            self.sel = max(0, n - 1)
        self.footer(stdscr, w, h)
        stdscr.refresh()

    def header(self, stdscr, snap, w) -> None:
        bits = [snap.device or "no device"]
        if snap.soc:
            bits.append(snap.soc)
        if snap.pmaports_branch:
            bits.append(snap.pmaports_branch)
        left = f" porthole {T.g('sep')} " + f" {T.g('sep')} ".join(bits)
        # Kernel-timestamp format, so what you did in here lines up with the
        # kmsg you are tailing. That correlation is the most common thing a
        # porter does by hand.
        clock = f"[{self.model.uptime():9.2f}] "
        state = (snap.state or "unprobed").upper()
        stdscr.addstr(0, 0, " " * max(0, w - 1), T.attr(T.BASE, reverse=True))
        stdscr.addstr(0, 0, T.fit(left, max(0, w - len(clock) - len(state) - 4)),
                      T.attr(T.BASE, reverse=True, bold=True))
        role = T.OK if state in ("BOOTED", "SSH") else T.WARN
        if w > len(clock) + len(state) + 4:
            stdscr.addstr(0, w - len(clock) - len(state) - 2, state,
                          T.attr(role, reverse=True))
            stdscr.addstr(0, w - len(clock) - 1, clock,
                          T.attr(T.BASE, reverse=True))

    def footer(self, stdscr, w, h) -> None:
        if self.message:
            stdscr.addstr(h - 1, 0, T.fit(" " + self.message, w - 1),
                          T.attr(self.message_role))
            return
        tabs = "  ".join(f"{i + 1} {n}" for i, (n, _) in enumerate(PANES))
        hint = f" {tabs}    / palette    r refresh    ? keys    q quit"
        if self.model.busy:
            hint += "   working…"
        stdscr.addstr(h - 1, 0, T.fit(hint, w - 1), T.attr(T.DIM))

    # --------------------------------------------------------------- key --

    def key(self, stdscr, ch) -> None:
        self.message = ""
        if self.in_palette or self.in_filter:
            return self.text_key(stdscr, ch)

        if ch in (ord("q"), 27):
            self.running = False
        elif ch == ord("/"):
            self.in_palette = True
            self.query = ""
            self.sel = 0
        elif ord("1") <= ch <= ord("5"):
            self.pane = ch - ord("1")
            self.sel = 0
            self.query = ""
        elif ch == ord("r"):
            self.model.refresh()
            self.items = palette.collect(self.model.snapshot)
            self.note("refreshing", T.DIM)
        elif ch in (curses.KEY_DOWN, ord("j")):
            self.move(1)
        elif ch in (curses.KEY_UP, ord("k")):
            self.move(-1)
        elif ch in (curses.KEY_NPAGE, 6):        # PgDn, ctrl-F
            self.move(self.page)
        elif ch in (curses.KEY_PPAGE, 2):        # PgUp, ctrl-B
            self.move(-self.page)
        elif ch in (curses.KEY_HOME, ord("g")):
            self.sel = 0
        elif ch in (curses.KEY_END, ord("G")):
            self.sel = max(0, self.count - 1)
        elif ch == ord("f") and PANES[self.pane][0] in ("tools", "brain"):
            self.in_filter = True
            self.query = ""
        elif ch in (curses.KEY_ENTER, 10, 13):
            self.activate(stdscr)

    def text_key(self, stdscr, ch) -> None:
        if ch == 27:
            self.in_palette = self.in_filter = False
            self.query = ""
        elif ch in (curses.KEY_ENTER, 10, 13):
            if self.in_palette:
                self.activate(stdscr)
            self.in_filter = False
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            self.query = self.query[:-1]
            self.sel = 0
        elif ch in (curses.KEY_DOWN, 14):        # down, ctrl-N
            self.move(1)
        elif ch in (curses.KEY_UP, 16):          # up, ctrl-P
            self.move(-1)
        elif ch in (curses.KEY_NPAGE, 6):
            self.move(self.page)
        elif ch in (curses.KEY_PPAGE, 2):
            self.move(-self.page)
        elif 32 <= ch < 127:
            self.query += chr(ch)
            self.sel = 0

    # ------------------------------------------------------------ actions --

    def activate(self, stdscr) -> None:
        snap = self.model.snapshot
        if self.in_palette:
            hits = palette.rank(self.items, self.query)
            if not hits or self.sel >= len(hits):
                return
            self.in_palette = False
            return self.launch(stdscr, hits[self.sel]["run"])

        name = PANES[self.pane][0]
        if name == "devices":
            if self.sel < len(snap.devices):
                target = snap.devices[self.sel]
                self.launch(stdscr, f"porthole use {target}", quiet=True)
                self.model.refresh(device=target, block=True)
                self.items = palette.collect(self.model.snapshot)
                self.note(f"now on {target}", T.OK)
        elif name == "port":
            nxt = (snap.summary or {}).get("next")
            if nxt and nxt.get("command"):
                self.launch(stdscr, nxt["command"], safe=bool(nxt.get("safe")))

    def launch(self, stdscr, command, safe=False, quiet=False) -> None:
        """Run a porthole command, with the confirmation boundary applied."""
        if not command or not command.startswith("porthole "):
            self.note("that step is a description, not a command", T.WARN)
            return
        risky = any(w in command for w in DANGEROUS)
        if not quiet and needs_confirmation(command, safe):
            if not self.confirm(stdscr, command, risky):
                return
        self.shell(stdscr, command)

    def confirm(self, stdscr, command, risky) -> bool:
        """Full screen, exact command, explicit key. No y/n muscle memory."""
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        title = "This can change the device" if risky else "Run this?"
        stdscr.addstr(2, 2, title, T.attr(T.CRIT if risky else T.BASE,
                                          bold=True))
        stdscr.addstr(4, 4, T.fit(command, w - 8), T.attr(T.NOTE, bold=True))
        if risky:
            stdscr.addstr(6, 2, "A bad image on the wrong slot can leave this "
                                "device unbootable.", T.attr(T.WARN))
            stdscr.addstr(7, 2, "Check the slot policy before you agree.",
                          T.attr(T.WARN))
        stdscr.addstr(9, 2, "press y to run, any other key to cancel",
                      T.attr(T.DIM))
        stdscr.refresh()
        stdscr.nodelay(False)
        ch = stdscr.getch()
        stdscr.nodelay(True)
        return ch == ord("y")

    def shell(self, stdscr, command) -> None:
        """Drop out of curses, run it, come back. The output belongs to the
        command, and paging it through a pane would mangle anything
        interactive."""
        argv = shlex.split(command)
        curses.endwin()
        print(f"\n$ {command}\n")
        try:
            subprocess.run([sys.executable,
                            str(self.root / "bin" / "porthole"), *argv[1:]])
        except OSError as exc:
            print(f"could not run it: {exc}")
        input("\n[enter] back to porthole ")
        stdscr.clear()
        curses.doupdate()
        self.model.refresh()

    def note(self, text, role) -> None:
        self.message, self.message_role = text, role


class _Sub:
    """Shifts a pane's coordinates below the header, so panes draw from y=0."""

    def __init__(self, win, dy, height):
        self.win, self.dy, self.height = win, dy, height

    def addstr(self, y, x, text, attr=0):
        if 0 <= y < self.height:
            try:
                self.win.addstr(y + self.dy, x, text, attr)
            except curses.error:
                pass                      # bottom-right cell; harmless


def main(argv, root) -> int:
    device = None
    if "-d" in argv:
        device = argv[argv.index("-d") + 1]
    elif "--device" in argv:
        device = argv[argv.index("--device") + 1]
    if not sys.stdout.isatty():
        print("porthole-tui needs a terminal. For a pipe, use `porthole next "
              "--json`.", file=sys.stderr)
        return 64
    os.environ.setdefault("ESCDELAY", "25")   # esc must feel instant
    app = App(root, device)
    return curses.wrapper(app.run)
