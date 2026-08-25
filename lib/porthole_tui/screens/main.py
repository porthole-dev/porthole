# SPDX-License-Identifier: MIT
"""The main screen: rail, content, drawer.

One screen with a swappable content region rather than six screens, because
the rail must stay on screen across every section -- pushing a screen per
section would take the spine away exactly when it is most useful.

Sections 1-4 (port, devices, tools, brain) have real catalogue widgets.
Sections 5-6 (logs, jobs) still show the placeholder below -- their widgets
land in a later task, and an empty pane reads as a broken tool where a
sentence does not.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Label

from ..widgets.brain import NoteList
from ..widgets.devices import DeviceList
from ..widgets.jobs import JobDrawer
from ..widgets.port import PortView
from ..widgets.rail import Rail
from ..widgets.tools import ToolList


class MainScreen(Screen):
    # No tab/shift+tab bindings here: Screen already ships them qualified as
    # 'app.focus_next' / 'app.focus_previous'. Redeclaring them unqualified
    # replaces those correct defaults with ones that resolve against THIS
    # class -- which has no action_focus_next -- and silently does nothing.
    # Ruling R21: measured dead (focus never moved) before this was deleted.
    BINDINGS = [
        Binding("r", "refresh", "refresh"),
        Binding("question_mark", "help", "keys", key_display="?"),
        Binding("q", "quit", "quit"),
        # The drawer's own keys live here, not on JobDrawer: a Binding on a
        # WIDGET only resolves while focus sits inside that widget's own
        # subtree, and JobDrawer docks outside #content -- with the normal
        # focus on a catalogue's #rows, a key bound on JobDrawer itself would
        # never appear in the resolution chain (ruling R20, measured with a
        # pilot probe). MainScreen is always in the chain, so the actions
        # below just delegate into the drawer.
        Binding("ctrl+j", "toggle_drawer", "job drawer"),
        # A verb's own default was all the old palette could reach: it built
        # `porthole <verb>` and stopped. action_edit_args below opens the
        # generated form for whatever is selected, defined on THIS class so
        # it resolves without an `app.` prefix (ruling R20).
        Binding("e", "edit_args", "args"),
        # PortholeApp sets ENABLE_COMMAND_PALETTE = False so this key is free:
        # Textual's own App installs a PRIORITY binding on ctrl+p for its own
        # command_palette action whenever that flag is left at its True
        # default, and a priority binding on the App wins over a normal one
        # declared here on the Screen regardless of focus -- confirmed with a
        # pilot probe, the same way ruling R20 keeps finding these.
        Binding("ctrl+p", "palette", "palette"),
        # ctrl+c is otherwise claimed by Textual itself: App binds it to
        # help_quit (a "press q to quit" nag) and Screen binds it to
        # copy_text. Declaring it again here, in MainScreen's own BINDINGS,
        # wins over both -- confirmed with a pilot probe, not assumed.
        Binding("ctrl+c", "cancel_job", "cancel job", show=False),
        Binding("ctrl+r", "rerun_job", "re-run", show=False),
    ]

    section = reactive("port")

    def __init__(self, sections, **kw):
        super().__init__(**kw)
        self.sections = sections
        self.reader_open = False
        # Section widgets are mounted once and then shown/hidden. Rebuilding a
        # 95-row catalogue on every switch measured 1715ms; keeping them alive
        # makes a switch a display toggle. Snapshots are only pushed into the
        # VISIBLE one, so four hidden catalogues do not each rebuild on a poll.
        self._mounted = {}
        # Logs and jobs keep the placeholder below until a later task.
        self.WIDGETS = {"port": PortView, "devices": DeviceList,
                        "tools": ToolList, "brain": NoteList}
        for index, (key, _title) in enumerate(sections, start=1):
            self._bindings.bind(str(index), "section('{}')".format(key),
                                show=False)

    def compose(self) -> ComposeResult:
        yield Label("", id="header")
        # snapshot.error was caught into the model so a broken profile
        # "degrades the display, never takes the session down" -- and then
        # degraded to blank panes with no explanation. This is where it says
        # so. Hidden entirely while there is nothing to report.
        yield Label("", id="snapshot-error", classes="error")
        with Horizontal(id="body"):
            yield Rail(self.sections, id="rail")
            yield Vertical(id="content")
        yield JobDrawer(id="drawer")
        yield Footer()

    def on_mount(self) -> None:
        self._show(self.section)
        self._paint_header(self.app.store.snapshot)
        self.app.store.refresh()
        self.set_interval(0.4, self._poll)

    def _show(self, key) -> None:
        """Switch section by toggling visibility, not by rebuilding.

        The first version removed every child and mounted a fresh catalogue,
        which rebuilt 95 rows of widgets each time -- 1715ms per switch,
        measured. Widgets are cheap to keep and expensive to make, so they are
        made once and hidden thereafter.
        """
        content = self.query_one("#content")
        widget = self._mounted.get(key)
        if widget is None:
            factory = self.WIDGETS.get(key)
            if factory is None:
                # Every section is a placeholder until its widget lands.
                # Saying so beats drawing an empty pane, which reads as broken.
                # Focusable, like the real widgets it stands in for: focus must
                # never have nowhere to land.
                widget = Label("{} arrives in a later release".format(key),
                               classes="empty")
                widget.can_focus = True
            else:
                widget = factory()
            self._mounted[key] = widget
            content.mount(widget)
        for other, mounted in self._mounted.items():
            mounted.display = (other == key)
        # A hidden widget is not polled, so it may hold a stale snapshot.
        # Refresh it as it comes back into view rather than on every tick.
        if hasattr(widget, "snapshot"):
            snap = self.app.store.snapshot
            if getattr(widget.snapshot, "stamp", None) != snap.stamp:
                widget.snapshot = snap
        widget.focus()

    def watch_section(self, _old, new) -> None:
        if self.is_mounted:
            self._show(new)
            self.query_one(Rail).section = new

    def _poll(self) -> None:
        """Publish the snapshot into the widgets when the worker replaces it.

        Polling a stamp rather than a callback: the Store is thread-based, and
        a worker thread calling into Textual widgets directly is the class of
        bug the old model's comments warn about.
        """
        snap = self.app.store.snapshot
        self._paint_header(snap)
        rail = self.query_one(Rail)
        if rail.snapshot is None or rail.snapshot.stamp != snap.stamp:
            rail.snapshot = snap
            # Only the visible one: hidden catalogues are refreshed by
            # _show as they come back into view, so a poll never triggers
            # four rebuilds for three panes nobody is looking at.
            widget = self._mounted.get(self.section)
            if widget is not None and hasattr(widget, "snapshot"):
                widget.snapshot = snap

    def _paint_header(self, snap) -> None:
        """device · soc · state, and the session clock.

        The curses console showed `google-cheetah · gs201 · BOOTED`; the
        Textual one computed all three every refresh and rendered them
        nowhere, which is the orphaned-pane pattern this branch deleted,
        reproduced inside the branch that deleted it. It also has a safety
        edge: `porthole flash boot --slot b` does not name the phone it is
        aimed at, and until this line nothing else on screen did either.

        The clock is the SESSION clock, in kernel-timestamp format on
        purpose: it lets you line up what you did in here against a kmsg you
        are tailing, which is the single most common correlation a porter
        makes. It is labelled `session` because an unlabelled `[  0.43]`
        reads as a device uptime that is implausibly low, rather than as
        "you opened this console 26 seconds ago".
        """
        from rich.text import Text
        from .. import ink

        line = Text()
        line.append(snap.device or "no device",
                    style=ink.ACTIVE if snap.device else ink.WARN)
        line.append("  ·  ", style=ink.DIM)
        line.append(snap.soc or "unknown soc", style=ink.DIM)
        line.append("  ·  ", style=ink.DIM)
        line.append_text(ink.state(snap.state))
        if snap.pmaports_branch:
            line.append("  ·  ", style=ink.DIM)
            line.append(snap.pmaports_branch, style=ink.DIM)
        line.append("   session ", style=ink.DIM)
        line.append("[{:>8.2f}]".format(self.app.store.uptime()), style=ink.DIM)
        self.query_one("#header", Label).update(line)

        error = self.query_one("#snapshot-error", Label)
        error.update(snap.error or "")
        error.display = bool(snap.error)

    def action_section(self, key: str) -> None:
        self.section = key

    def action_quit(self) -> None:
        # A key bound at Screen level dispatches to a method on the SCREEN,
        # not the App -- Screen has no action_quit of its own, so "q" pressed
        # nothing at all until this existed. Delegate explicitly.
        self.app.exit()

    def action_refresh(self) -> None:
        self.app.store.refresh()
        self.notify("refreshing")

    def action_help(self) -> None:
        from .help import HelpScreen
        self.app.push_screen(HelpScreen())

    def action_toggle_drawer(self) -> None:
        self.query_one(JobDrawer).action_toggle()

    def action_cancel_job(self) -> None:
        self.query_one(JobDrawer).action_cancel()

    def action_rerun_job(self) -> None:
        self.query_one(JobDrawer).action_rerun()

    def action_edit_args(self) -> None:
        """Open the argument form for whatever is selected.

        `porthole blobs unsparse vendor.img` was unreachable before this: the
        old palette offered only a verb's bare default. Explicit shapes, not
        duck-typing (ruling R24) -- a milestone is a dict with "how", a tool
        has a .summary, and anything else declines rather than guessing.
        """
        import porthole_cli
        from ..widgets.catalogue import Catalogue
        from .form import ArgForm
        widget = next(iter(self.query(Catalogue)), None)
        if widget is None:
            return
        listing = widget.query_one("#rows")
        index = getattr(listing, "index", None)
        payloads = widget._payloads
        target = payloads[index] if (index is not None
                                     and index < len(payloads)) else None
        verb = None
        if hasattr(target, "summary"):                      # a tool
            verb = "run"
        elif isinstance(target, dict) and \
                (target.get("how") or "").startswith("porthole "):
            verb = target["how"].split()[1]
        if verb is None:
            self.notify("nothing here takes arguments", severity="warning")
            return
        specs = {s["verb"]: s for s in porthole_cli.discover(self.app.root)}
        if verb not in specs:
            self.notify("nothing here takes arguments", severity="warning")
            return

        def built(command):
            if command:
                self.launch(command, safe=False)

        self.app.push_screen(ArgForm(specs[verb]), built)

    def action_palette(self) -> None:
        """Everything reachable in two keystrokes.

        A porter does not think "is what I want a verb or a tool", they think
        "suspend" and want whatever matches -- so the palette searches all
        four namespaces at once. The old palette built `porthole <verb>` for
        every verb and ran it, reaching a verb's default and nothing past it;
        an item that takes arguments now opens its form instead.
        """
        from ..content import for_milestone, for_note, for_tool
        from ..widgets.palette import Palette, collect
        from .form import ArgForm
        from .reader import Reader

        def chosen(item):
            if not item:
                return
            if item["kind"] == "verb" and item["needs_args"]:
                def built(command):
                    if command:
                        self.launch(command, safe=False)
                self.app.push_screen(ArgForm(item["spec"]), built)
                return
            if item["kind"] == "note":
                self.app.push_screen(Reader(for_note(self.app.root, item["id"])))
                return
            if item["kind"] == "tool":
                doc = for_tool(self.app.root, item["id"])

                def ran_tool(command):
                    if command:
                        self.launch(command, safe=False)
                self.app.push_screen(Reader(doc), ran_tool)
                return
            if item["kind"] == "milestone":
                doc = for_milestone(item["row"])

                def ran_step(command):
                    if command:
                        self.launch(command, safe=doc.safe)
                self.app.push_screen(Reader(doc), ran_step)
                return
            self.launch(item["run"], safe=False)

        self.app.push_screen(
            Palette(collect(self.app.root, self.app.store.snapshot)), chosen)

    def launch(self, command, safe=False) -> None:
        """Run a porthole command, with the confirmation boundary applied.

        Refuses anything that does not start with `porthole `: a milestone's
        `how` field is sometimes prose ("read the panel datasheet"), and prose
        must never reach a shell.
        """
        from ..safety import is_risky, needs_confirmation
        from .confirm import ConfirmRun
        if not command or not command.startswith("porthole "):
            self.notify("that step is a description, not a command",
                        severity="warning")
            return
        if not needs_confirmation(command, safe):
            self._spawn(command)
            return

        def answered(agreed):
            if agreed:
                self._spawn(command)

        # The device goes in the dialog: `porthole flash boot --slot b`
        # reproduces exactly and still does not name the phone it is aimed
        # at, and that is the one dialog where that has to be on screen.
        self.app.push_screen(
            ConfirmRun(command, is_risky(command),
                       self.app.store.snapshot.device), answered)

    def _spawn(self, command) -> None:
        from ..jobs import is_interactive
        if is_interactive(self.app.root, command):
            self._suspend_and_run(command)
            return
        # on_exit, or the model goes stale behind a job that changed it:
        # _suspend_and_run refreshes when it comes back, this path did not,
        # and _poll only republishes when the stamp moves. Choosing a device
        # in the devices pane ran `porthole use` successfully while the rail,
        # the port pane and the `*` marker kept showing the old device until
        # the user pressed r.
        job = self.app.jobs.spawn(
            command, on_exit=lambda _job: self.app.store.refresh())
        self.query_one(JobDrawer).attach(job)
        self.notify("running: {}".format(command))

    def _suspend_and_run(self, command) -> None:
        """Hand the real terminal over.

        `porthole serial console` drives termios directly and `porthole
        init` prompts in a loop -- piping either into the drawer would
        mangle the former and make the latter look hung with no visible
        prompt. The output belongs to the command.

        The body of this `with` must never raise (ruling R26). Textual
        8.2.8's App.suspend() is a @contextmanager with no try/finally --
        resume_application_mode() sits after a bare yield -- so an exception
        escaping here skips terminal restoration entirely and leaves the user
        in raw mode with no way back. Ctrl-D at the prompt is an ordinary
        habit, not a contrived input.
        """
        import os
        import shlex
        import subprocess
        import sys
        argv = shlex.split(command)
        # exec has no shell, so a literal `~` would reach the tool
        # unexpanded -- the same reason jobs.py's _run() does this.
        argv = [os.path.expanduser(a) for a in argv]
        with self.app.suspend():
            try:
                print("\n$ {}\n".format(command))
                subprocess.run([sys.executable,
                                str(self.app.root / "bin" / "porthole")]
                               + argv[1:])
            except OSError as exc:
                print("could not run it: {}".format(exc))
            except KeyboardInterrupt:
                print("\ninterrupted")
            try:
                input("\n[enter] back to porthole ")
            except (EOFError, KeyboardInterrupt):
                pass                    # Ctrl-D / Ctrl-C here just means "go back"
        # An interactive command may well have changed the device's state.
        # Outside the `with`, so it runs after restoration, not during
        # suspension.
        self.app.store.refresh()

    def on_catalogue_chosen(self, event) -> None:
        from ..content import for_milestone, for_note, for_tool
        from .reader import Reader
        payload = event.payload
        # Explicit shapes, not duck-typing (ruling R24). Tool and Note
        # currently have disjoint attributes so hasattr() on one happened to
        # work, but that is luck: give Tool a .body and every tool would
        # silently open as a note. A device must be an actual str, and
        # anything unrecognised is refused rather than formatted into a
        # command -- launch() checks the PREFIX, not the sense, so
        # "porthole use {'id': ...}" would sail straight through its gate.
        if isinstance(payload, dict) and "phase" in payload:
            doc = for_milestone(payload)
        elif isinstance(payload, str):
            self.launch("porthole use {}".format(payload), safe=True)
            return
        elif hasattr(payload, "body") and hasattr(payload, "meta"):
            doc = for_note(self.app.root, payload.id)
        elif hasattr(payload, "summary") and hasattr(payload, "needs"):
            doc = for_tool(self.app.root, payload.name)
        else:
            self.notify("cannot open that selection", severity="warning")
            return

        def closed(command):
            self.reader_open = False
            if command:
                self.launch(command, safe=getattr(doc, "safe", False))

        self.reader_open = True
        self.app.push_screen(Reader(doc), closed)
