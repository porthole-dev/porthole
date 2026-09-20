#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: device:google-taimen
# needs: on-device, inside the graphical session (GTK4 + pygobject)
# env: -
# exits: 0 ok
"""What frame interval does the compositor actually give a client?

WHY THIS EXISTS
  The half-rate command-mode lock on this panel is a SESSION property, not a
  video one: UI and video are equally affected, so every video-shaped
  instrument (decode rate, DPU vsync counters, dropped-frame counts) measures
  it only indirectly and disagrees with itself. This measures it directly.

  An earlier copy of this probe lived only in /tmp and was lost, and the 30 fps
  question then had to be re-derived from scratch a month later. Hence a real
  tool in the repo.

  Read brain/findings/the-session-is-back-to-30fps-on-7-2-and-ctl-start-is-not-why
  before acting on a 30 ms result.

NO VIDEO: damage a widget every frame, time the frame-clock callbacks. That
isolates the display path from decode, GStreamer and buffer pools.

  ~16.7 ms -> this CLIENT is painting at the panel's 60 Hz
  ~33.4 ms -> this CLIENT is at half rate

WHAT THIS DOES NOT MEASURE, and it cost a session on 2026-09-20: it is NOT
the session's frame rate and never was. The number is this probe's own paint
cost, and on taimen that paint is the limiter. Measured the same minute, same
compositor, same panel:

    tickonly  (frame clock, no damage)   58.6 / 59.3 fps
    this probe (clock + its repaint)     30.4 / 30.5 fps

So it reported ~31 fps and an agent concluded the whole session was locked at
half rate -- while the operator, looking at the phone, saw phosh scrolling
smoothly. It also reports ~31 fps with the PANEL SWITCHED OFF, where vsync
does not move at all. A number that survives the display being powered down
is not a number about the display.

Hence --control, ON BY DEFAULT: an identical window that ticks and paints
NOTHING runs first, and its rate is the frame clock the compositor is
actually handing out. If the control is ~60 and the damaged run is ~30, the
limiter is in here, not in the session. If BOTH are low, the clock itself is
throttled and that is a real finding. Reading the second number without the
first is what produced the wrong conclusion.

Always check the panel is ON either side of a run (bl_power, and
card0-DSI-1/enabled) -- see
brain/findings/the-30fps-lock-is-not-power-the-panel-runs-at-60.

Damage is done by swapping a CSS class, NOT a cairo draw handler: pygobject
here has no cairo.Context converter, so a draw_func raises every frame and the
probe silently measures a window that never painted.
"""
import sys, time
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib, Gdk

DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
# The control runs unless explicitly waived. Waiving it is how the wrong
# conclusion gets drawn again, so it takes a word, not a flag character.
CONTROL = "--no-control" not in sys.argv
stamps = []
control_fps = None

CSS = b"""
.a { background: linear-gradient(90deg, #f00, #00f); }
.b { background: linear-gradient(90deg, #0f0, #ff0); }
"""


def run_control(seconds):
    """The frame clock with NO damage: what the compositor is handing out.

    Same window, same tick callback, no CSS swap and no paint. Whatever this
    reaches is the ceiling the session offers; the damaged run below can only
    be lower, and the gap between them is THIS PROBE's cost, not the
    session's.
    """
    ticks = []

    class Control(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="dev.porthole.framprobe.control")
            self.connect("activate", self.up)

        def up(self, *_):
            win = Gtk.ApplicationWindow(application=self)
            win.set_default_size(400, 400)
            box = Gtk.Box()
            win.set_child(box)
            box.add_tick_callback(lambda *_a: (ticks.append(time.monotonic())
                                               or GLib.SOURCE_CONTINUE))
            win.present()
            GLib.timeout_add(int(seconds * 1000), self.done)

        def done(self):
            self.quit()
            return GLib.SOURCE_REMOVE

    Control().run([])
    if len(ticks) < 3:
        return None
    return (len(ticks) - 1) / (ticks[-1] - ticks[0])


def report():
    if control_fps is not None:
        print(f"control (no damage) = {control_fps:.1f} fps"
              "   <- the frame clock the compositor gives out")
    if len(stamps) < 3:
        print("too few frames -- did the window map?")
        return
    gaps = sorted((b - a) * 1000.0 for a, b in zip(stamps, stamps[1:]))
    n = len(gaps)
    p = lambda q: gaps[min(n - 1, int(n * q))]
    span = stamps[-1] - stamps[0]
    print(f"frames={n+1} over {span:.1f}s = {n/span:.1f} fps")
    print(f"interval  min={gaps[0]:.1f}  p50={p(.50):.1f}  p90={p(.90):.1f}  "
          f"p99={p(.99):.1f}  max={gaps[-1]:.1f} ms")
    for label, lo, hi in (("<20ms  (60Hz)", 0, 20), ("20-45ms (30Hz lock)", 20, 45),
                          (">45ms  (worse)", 45, 1e9)):
        c = sum(1 for g in gaps if lo <= g < hi)
        print(f"  {label:22s} {c:5d}  {100.0*c/n:5.1f}%")
    # The verdict, so nobody has to remember which number means what.
    if control_fps is None:
        print("NO CONTROL RAN -- this number alone cannot tell you whether "
              "the session or this probe is the limiter.")
    elif control_fps > 1.4 * (n / span):
        print(f"THIS PROBE is the limiter: the clock offered "
              f"{control_fps:.0f} fps and the damaged run reached "
              f"{n/span:.0f}. Not a session frame-rate result.")
    else:
        print(f"the clock itself is at {control_fps:.0f} fps, so the limit is "
              f"NOT this probe's paint -- that is a real session finding.")


class App(Gtk.Application):
    def do_activate(self):
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), prov,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        win = Gtk.ApplicationWindow(application=self, title="framprobe")
        win.fullscreen()
        box = Gtk.Box()
        box.set_hexpand(True); box.set_vexpand(True)
        box.add_css_class("a")
        win.set_child(box)

        def on_tick(widget, clock):
            stamps.append(time.monotonic())
            if len(stamps) & 1:
                widget.remove_css_class("a"); widget.add_css_class("b")
            else:
                widget.remove_css_class("b"); widget.add_css_class("a")
            return GLib.SOURCE_CONTINUE

        box.add_tick_callback(on_tick)
        win.present()
        GLib.timeout_add(int(DUR * 1000), self.finish)

    def finish(self):
        report()
        self.quit()
        return GLib.SOURCE_REMOVE


if CONTROL:
    control_fps = run_control(min(DUR, 4.0))

App().run([])
