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

  ~16.7 ms -> the session runs at the panel's 60 Hz
  ~33.4 ms -> the half-rate command-mode lock

Damage is done by swapping a CSS class, NOT a cairo draw handler: pygobject
here has no cairo.Context converter, so a draw_func raises every frame and the
probe silently measures a window that never painted.
"""
import sys, time
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib, Gdk

DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
stamps = []

CSS = b"""
.a { background: linear-gradient(90deg, #f00, #00f); }
.b { background: linear-gradient(90deg, #0f0, #ff0); }
"""


def report():
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


App().run([])
