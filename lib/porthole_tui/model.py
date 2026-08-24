# SPDX-License-Identifier: MIT
"""The warm model: everything the TUI knows, loaded once and refreshed quietly.

This is the whole reason a TUI is faster than the CLI rather than merely
prettier. A `porthole next` pays ~70ms to start an interpreter, resolve config,
read the registry and evaluate 26 milestone probes. A TUI pays that once and
then answers keystrokes from memory.

Two rules keep it honest:

**Nothing here blocks the event loop.** Refresh runs on a worker thread and
publishes a new immutable snapshot when it finishes; the UI always renders the
last complete snapshot. A half-updated model on screen is worse than a stale
one, because you cannot tell which half is which.

**Nothing here touches the device ON THE EVENT LOOP.** The state probe runs
here, on the worker, with a bounded timeout -- that is what the rule always
meant. Refusing to probe at all is what made the header read UNPROBED forever
while three milestones told the user to leave the TUI and run `doctor`.
"""
from __future__ import annotations

import pathlib
import threading
import time


class Snapshot:
    """One complete, consistent view. Replaced wholesale, never mutated."""

    __slots__ = ("device", "devices", "cfg", "rows", "summary", "has_markers",
                 "pmaports", "pmaports_branch", "workdir", "soc", "state",
                 "error", "stamp",
                 # The catalogues. They belong here because the panes were
                 # re-reading 95 tool headers and 55 notes inside render(),
                 # which runs on every frame -- 14.3% of a core, idle, to
                 # re-parse files that had not changed.
                 "tools", "notes")

    def __init__(self, **kw):
        for name in self.__slots__:
            setattr(self, name, kw.get(name))


class Model:
    """Owns the snapshot and the thread that refreshes it."""

    def __init__(self, root, device=None):
        self.root = pathlib.Path(root)
        self._device = device
        self._lock = threading.Lock()
        self._snapshot = Snapshot(device=device or "", devices=[], rows=[],
                                  summary={}, tools=[], notes=[],
                                  error=None, stamp=0.0)
        self._busy = False
        self.started = time.monotonic()

    # ------------------------------------------------------------- access --

    @property
    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    @property
    def busy(self) -> bool:
        return self._busy

    def uptime(self) -> float:
        """Seconds since the session began.

        Rendered in the header in kernel-timestamp format. That is not
        decoration: it lets you line up what you did in here against the
        timestamps in a kmsg you are tailing, which is the single most common
        correlation a porter makes.
        """
        return time.monotonic() - self.started

    # ------------------------------------------------------------ refresh --

    def refresh(self, device=None, block=False) -> None:
        if device is not None:
            self._device = device
        if block:
            self._load()
            return
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            snap = self._build()
        except Exception as exc:  # noqa: BLE001
            # A broken profile or an absent pmaports must degrade the display,
            # never take the session down mid-port.
            snap = Snapshot(device=self._device or "", devices=[], rows=[],
                            summary={}, tools=[], notes=[],
                            error=f"{type(exc).__name__}: {exc}",
                            stamp=time.time())
        with self._lock:
            self._snapshot = snap
        self._busy = False

    def _build(self) -> Snapshot:
        import porthole
        import porthole_milestones as ms
        import porthole_pmaports as pmap
        import porthole_cmd_next as nxt

        devices = porthole.list_profiles(self.root)
        env = {"PORTHOLE_DEVICE": self._device} if self._device else {}
        cfg = porthole.load_config(root=self.root, env=env)
        device = cfg.get("PORTHOLE_DEVICE", "")

        ctx = _Ctx(self.root, cfg)
        rows, summary, has_markers = [], {}, True
        if device:
            rows, summary, has_markers = nxt.collect(ctx)

        # Read once, here, rather than per frame in the panes.
        import porthole_cmd_tools as tmod
        import porthole_cmd_brain as bmod
        try:
            tools = tmod.collect(self.root, device)
        except Exception:  # noqa: BLE001
            tools = []
        try:
            notes = bmod.load_notes(self.root)
        except Exception:  # noqa: BLE001
            notes = []

        pm = pmap.find_pmaports(cfg)
        branch = _branch(pm) if pm else ""

        # The device state is a probe, and probes belong on this thread -- the
        # header said UNPROBED forever because the model refused to ask, while
        # three milestones told the user to leave the TUI and run doctor. The
        # rule was never "no device I/O", it was "no device I/O on the event
        # loop", and this is the worker.
        state = cfg.get("TK_DEVICE_STATE", "") or ""
        if not state:
            try:
                state = porthole.Device(cfg).state(max_age=20)
            except Exception:  # noqa: BLE001
                state = "unknown"

        return Snapshot(
            device=device, devices=devices, cfg=cfg, rows=rows,
            summary=summary, has_markers=has_markers,
            pmaports=str(pm) if pm else "", pmaports_branch=branch,
            workdir=cfg.get("PORTHOLE_WORKDIR", ""),
            soc=cfg.get("PORTHOLE_SOC", ""),
            state=state or "unknown", tools=tools, notes=notes,
            error=None, stamp=time.time())


class _Ctx:
    """The minimum a milestone probe or a command needs.

    Deliberately not the CLI's Ctx: that one carries argparse namespaces and
    output helpers the TUI has no use for, and constructing one here would tie
    the model to the shape of a parser.
    """

    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.out = _Silent()

    def emit(self, payload, render=None):
        return 0


class _Silent:
    """Swallows CLI-style output. The TUI owns the screen; a stray print
    through curses corrupts it and the corruption outlives the message."""

    def __getattr__(self, _):
        return lambda *a, **k: None

    def __call__(self, *a, **k):
        return None

    def paint(self, text, _colour):
        return text

    def sym(self, fancy, plain):
        return plain


def _branch(pmaports) -> str:
    import subprocess
    try:
        return subprocess.run(
            ["git", "-C", str(pmaports), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def phases(rows) -> list[dict]:
    """Milestones folded into the phase spine.

    The spine is the TUI's signature, and it is honest: bring-up genuinely is a
    sequence, so ordering carries information rather than decorating.
    """
    import porthole_milestones as ms
    out, seen = [], {}
    for row in rows:
        p = seen.get(row["phase"])
        if p is None:
            p = {"name": row["phase"], "done": 0, "total": 0, "blocked": 0,
                 "stale": 0, "current": False}
            seen[row["phase"]] = p
            out.append(p)
        p["total"] += 1
        if row["state"] == ms.DONE:
            p["done"] += 1
        elif row["state"] == ms.BLOCKED:
            p["blocked"] += 1
        if row["source"] == "stale":
            p["stale"] += 1
    for p in out:
        if p["done"] < p["total"]:
            p["current"] = True
            break
    return out
