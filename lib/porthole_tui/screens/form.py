# SPDX-License-Identifier: MIT
"""The generated argument form.

Every verb already declares its arguments for argparse, and its description
and its examples too. All of that was visible only to someone who ran
`porthole <verb> --help` in a shell. Rendered here, the form IS the
explanation -- which is the answer to "it is not even clear it is a tool".

The old palette built `porthole <verb>` and stopped there, so it could reach
a verb's default behaviour and nothing past it: `blobs unsparse vendor.img`
was unreachable. This screen is what makes it reachable.
"""
from __future__ import annotations

import pathlib

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (Button, Checkbox, Footer, Input, Label, Select,
                             Static)

from .. import argspec

# Input's `type=` restricts what a keypress is even allowed to insert.
_INPUT_TYPE = {"int": "integer", "float": "number"}


def _picker_start(snapshot):
    """PORTHOLE_WORKDIR, then the device's own workdir, then cwd.

    Never blindly cwd: the image you want is next to the port, not next to
    wherever the terminal happens to be. `cfg["PORTHOLE_WORKDIR"]` already
    carries per-device fallbacks baked in by porthole.load_config -- the
    second lookup only matters when the snapshot's cfg does not (yet)
    reflect it, which is why it is worth keeping as a distinct fallback
    rather than trusting cfg alone.
    """
    cfg = (snapshot and snapshot.cfg) or {}
    workdir = cfg.get("PORTHOLE_WORKDIR")
    if not workdir and snapshot is not None:
        paths = (snapshot.device_paths or {}).get(snapshot.device) or {}
        workdir = paths.get("workdir")
    return workdir or pathlib.Path.cwd()


class ArgForm(ModalScreen):
    # Both actions are defined on THIS class, so they resolve against the
    # Screen namespace directly with no `app.` prefix needed -- ruling R20.
    BINDINGS = [
        Binding("escape", "cancel", "cancel"),
        Binding("ctrl+s", "submit", "run"),
    ]

    def __init__(self, spec, **kw):
        super().__init__(**kw)
        self.spec = spec
        self.fields = argspec.fields(spec)
        self.values = {}

    def command(self) -> str:
        return argspec.build(self.spec["verb"], self.fields, self.values)

    def use_example(self, example: str) -> None:
        values = argspec.parse_example(example, self.spec["verb"], self.fields)
        if values is not None:
            self.values = values
            self._sync()

    def compose(self) -> ComposeResult:
        yield Label("{} -- {}".format(self.spec["verb"],
                                      self.spec.get("help", "")),
                    id="reader-title")
        with VerticalScroll(id="form-body"):
            description = self.spec.get("description", "")
            if description:
                yield Static(description, classes="empty")
            if not self.fields:
                yield Static("This verb takes no arguments.", classes="empty")
            for field in self.fields:
                for widget in self._row(field):
                    yield widget
            verb = self.spec["verb"]
            # is_direct() matters here: five of this project's documented
            # examples are shell snippets (`cd "$(porthole cd)"`, a
            # `completion bash > file` redirect) that cannot decompose into
            # form fields. Offering one as a clickable "try:" row would be a
            # row that silently does nothing when pressed -- the exact
            # defect class (R20) this project keeps shipping.
            direct = [(i, ex) for i, ex in enumerate(self.spec.get("examples", []))
                     if argspec.is_direct(ex, verb)]
            if direct:
                yield Static("try:", classes="empty")
                for index, example in direct:
                    yield Button(example, id="ex-{}".format(index),
                                classes="form--example")
        yield Static(self.command(), id="form-command")
        # A modal fills the screen, so MainScreen's own footer is
        # covered while this is up. Without one here the keys below
        # are advertised nowhere at all -- and no modal binds `?`,
        # so help cannot be reached from one either.
        yield Footer()

    def _row(self, field):
        # A dest naming a slot or a partition (safety.DANGEROUS_FIELDS,
        # already folded into field.dangerous by argspec.fields()) renders
        # flagged: a generated form makes an irreversible command easier to
        # assemble than a bare verb ever was.
        out = [Label(field.label, classes="error" if field.dangerous else "")]
        if field.help:
            out.append(Label("  " + field.help, classes="empty"))
        ident = "f-" + field.dest
        if field.kind == "select":
            out.append(Select([(c, c) for c in field.choices], id=ident,
                              allow_blank=True))
        elif field.kind == "check":
            out.append(Checkbox("", id=ident))
        elif field.kind == "path":
            out.append(Horizontal(Input(id=ident),
                                  Button("browse", id="b-" + field.dest),
                                  classes="form--path-row"))
        else:
            out.append(Input(id=ident, type=_INPUT_TYPE.get(field.kind, "text")))
        return out

    def _sync(self) -> None:
        """Push self.values into the mounted widgets (values -> UI)."""
        for field in self.fields:
            try:
                widget = self.query_one("#f-" + field.dest)
            except Exception:  # noqa: BLE001 -- not mounted yet
                continue
            value = self.values.get(field.dest)
            if isinstance(widget, Checkbox):
                widget.value = bool(value)
            elif isinstance(widget, Select):
                widget.value = value if value else Select.NULL
            else:
                widget.value = "" if value is None else str(value)
        self._repaint()

    def _collect(self) -> None:
        """Read the mounted widgets into self.values (UI -> values).

        One generic sweep on ANY field changing, rather than each of
        on_input_changed/on_checkbox_changed/on_select_changed picking the
        dest out of its own event: Checkbox.Changed carries no public
        accessor for the widget that changed (only a private
        `_toggle_button`, inherited from ToggleButton.Changed) on textual
        8.2.8, so that per-event approach does not even run. Re-reading
        every field is a handful of query_one calls on a form with at most a
        few dozen rows, and it cannot go stale the way three independent,
        differently-shaped extractions can.
        """
        for field in self.fields:
            try:
                widget = self.query_one("#f-" + field.dest)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(widget, Checkbox):
                self.values[field.dest] = widget.value
            elif isinstance(widget, Select):
                value = widget.value
                self.values[field.dest] = "" if value is Select.NULL else value
            else:
                self.values[field.dest] = widget.value

    def _repaint(self) -> None:
        try:
            self.query_one("#form-command", Static).update(self.command())
        except Exception:  # noqa: BLE001
            pass

    def on_input_changed(self, event: Input.Changed) -> None:
        if (event.input.id or "").startswith("f-"):
            self._collect()
            self._repaint()

    def on_checkbox_changed(self, event) -> None:
        self._collect()
        self._repaint()

    def on_select_changed(self, event) -> None:
        self._collect()
        self._repaint()

    def on_button_pressed(self, event) -> None:
        ident = event.button.id or ""
        if ident.startswith("ex-"):
            self.use_example(self.spec["examples"][int(ident[3:])])
        elif ident.startswith("b-"):
            self._browse(ident[2:])

    def _browse(self, dest) -> None:
        from .picker import FilePicker
        start = _picker_start(self.app.store.snapshot)

        def chosen(path):
            if path:
                self.values[dest] = path
                self._sync()

        self.app.push_screen(FilePicker(start), chosen)

    def action_submit(self) -> None:
        self.dismiss(self.command())

    def action_cancel(self) -> None:
        self.dismiss(None)
