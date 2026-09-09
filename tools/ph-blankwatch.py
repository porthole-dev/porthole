#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device as the session user; grim; a screen that is ON (see
#        ph-ui.py unblank -- a blanked output makes grim block, not fail)
# env: -
# exits: 0 sampled · 1 grim never produced a frame
"""Sample how much of the screen is BLANK while something is happening.

WHY THIS EXISTS: "I scroll fast and half the screen goes white, then the
content fills back in" is a real, reproducible symptom with no number
attached to it. Every instrument this port had measured frame *timing*, and
a blank frame delivered on time is a perfect frame by every one of them.
This measures the pixels.

WHAT IT MEASURES: a row is "flat" if every sampled pixel in it is the same
colour. A band of flat rows in the middle of a web page is content that has
not been painted -- the compositor scrolled past what the backing store
covers. Legitimate whitespace is flat too, which is why the number to read
is the CHANGE between a still screen and a drag, not its absolute value.

    ph-blankwatch.py 8 > /tmp/blank.txt &     # sample for 8 s
    ...run the drag...
    wait

Output is one line per sample plus a summary; --json for a machine.
"""
import argparse, json, subprocess, sys, time, os, collections

ENV = dict(os.environ,
           XDG_RUNTIME_DIR=os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid()),
           WAYLAND_DISPLAY=os.environ.get("WAYLAND_DISPLAY", "wayland-0"))


def grab(scale):
    """One screencopy as a P6 PPM. Bounded: grim blocks forever on a DPMS-off
    output, and an unbounded call there looks exactly like a hung device."""
    # No -s: grim then captures at the output's NATIVE resolution. `-s 1`
    # means one pixel per LOGICAL unit, which on a 1440x2880 panel at scale 3
    # is 480x960 -- a downsample heavy enough that no row comes back flat and
    # this tool reports 0.000 for a screen that is visibly half blank.
    cmd = ["grim", "-t", "ppm", "-"] if not scale else \
          ["grim", "-s", str(scale), "-t", "ppm", "-"]
    try:
        p = subprocess.run(cmd, env=ENV, capture_output=True, timeout=15)
    except (subprocess.TimeoutExpired, OSError):
        # grim blocks, sometimes past any deadline, on an output whose content
        # is on a hardware plane -- measured while a YouTube video was playing,
        # which is exactly when the plane path is doing its job. A sampler that
        # dies there takes the whole arm with it, and the arm was measuring
        # something else. Skip the sample and keep going; the summary reports
        # how many were actually taken.
        return None
    if p.returncode or not p.stdout:
        return None
    return p.stdout


def parse_ppm(buf):
    """(width, height, pixels) from P6. Header fields are whitespace-separated
    and may be split across lines, so scan tokens rather than assuming a shape."""
    if not buf.startswith(b"P6"):
        return None
    i, fields = 2, []
    while len(fields) < 3:
        while i < len(buf) and buf[i:i + 1].isspace():
            i += 1
        if buf[i:i + 1] == b"#":                      # comment to end of line
            while i < len(buf) and buf[i:i + 1] != b"\n":
                i += 1
            continue
        j = i
        while j < len(buf) and not buf[j:j + 1].isspace():
            j += 1
        fields.append(int(buf[i:j]))
        i = j
    return fields[0], fields[1], buf[i + 1:]


def flat_rows(w, h, px, top, bottom, col_step, tol=6, edge=0.03):
    """(longest contiguous flat BAND, total flat fraction, modal colour).

    Read the BAND, not the total. The total counts every flat row anywhere,
    and on a text page that is mostly paragraph gaps: a perfectly rendered
    Wikipedia article measured 0.45 by the total and looked, in the saved
    frame, completely correct. Unpainted backing store is one CONTIGUOUS run
    hundreds of rows tall, so the longest run separates it from typography.

    Subsampled on purpose: this has to run at video rate on a phone. Slicing
    bytes with a step is C-speed; a per-pixel Python loop is not.

    Flat means max-min <= tol per channel, NOT exact equality. grim renders at
    the output's logical scale, so the frame is already a filtered downsample
    of the panel and a genuinely uniform region comes back with a unit or two
    of spread. Exact equality found zero flat rows on a plainly white
    Wikipedia page, which is how this tolerance got here.

    The outer `edge` of each row is dropped: the window's own border and the
    panel's rounded corners are not page content and would break every row.
    """
    y0, y1 = int(h * top), int(h * (1 - bottom))
    x0, x1 = int(w * edge), max(int(w * (1 - edge)), int(w * edge) + 1)
    flat, total, colours = 0, 0, collections.Counter()
    run = band = 0
    for y in range(y0, y1):
        row = px[(y * w + x0) * 3:(y * w + x1) * 3]
        total += 1
        for c in range(3):
            ch = row[c::3 * col_step]
            if not ch or max(ch) - min(ch) > tol:
                break
        else:
            flat += 1
            run += 1
            band = max(band, run)
            colours[(row[0], row[1], row[2])] += 1
            continue
        run = 0
    if not total:
        return 0.0, 0.0, None
    c = colours.most_common(1)[0][0] if colours else None
    return band / total, flat / total, c


def selftest():
    """One synthetic frame: top half flat white, bottom half noise."""
    w, h = 40, 40
    px = bytearray()
    for y in range(h):
        for x in range(w):
            px += b"\xff\xff\xff" if y < h // 2 else bytes([(x * 7) % 256, 3, 9])
    buf = b"P6\n%d %d\n255\n" % (w, h) + bytes(px)
    pw, ph, ppx = parse_ppm(buf)
    assert (pw, ph) == (w, h), (pw, ph)
    band, frac, colour = flat_rows(pw, ph, ppx, 0.0, 0.0, 1, edge=0.0)
    assert abs(frac - 0.5) < 1e-9, frac
    assert abs(band - 0.5) < 1e-9, band          # the flat half is one contiguous run
    assert colour == (255, 255, 255), colour
    # A row of one colour but sampled coarsely must not be called flat by luck.
    band, frac, _ = flat_rows(pw, ph, ppx, 0.5, 0.0, 1, edge=0.0)
    assert frac == 0.0 and band == 0.0, (frac, band)
    # Scattered flat rows (typography) must NOT read as a band.
    px2 = bytearray()
    for y in range(h):
        for x in range(w):
            px2 += b"\xff\xff\xff" if y % 4 == 0 else bytes([(x * 7) % 256, 3, 9])
    buf2 = b"P6\n%d %d\n255\n" % (w, h) + bytes(px2)
    pw2, ph2, ppx2 = parse_ppm(buf2)
    band, frac, _ = flat_rows(pw2, ph2, ppx2, 0.0, 0.0, 1, edge=0.0)
    assert abs(frac - 0.25) < 1e-9, frac
    assert abs(band - 1 / h) < 1e-9, band
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("seconds", type=float, nargs="?", default=8.0)
    ap.add_argument("--scale", type=float, default=0.0,
                    help="grim -s value; 0 (the default) means no -s at all, i.e. the "
                         "output's native resolution")
    ap.add_argument("--top", type=float, default=0.10,
                    help="fraction of the frame to ignore at the top (phosh panel, browser chrome)")
    ap.add_argument("--bottom", type=float, default=0.12,
                    help="fraction to ignore at the bottom (URL bar, gesture bar)")
    ap.add_argument("--tol", type=int, default=6,
                    help="per-channel spread still counted as flat")
    ap.add_argument("--col-step", type=int, default=4, help="sample every Nth pixel across a row")
    ap.add_argument("--save-worst", metavar="PATH",
                    help="write the blankest frame seen to PATH as a PNG. A number says "
                         "how much was blank; only the picture says WHAT was blank -- a band "
                         "at the leading edge, the whole viewport, or the chrome having moved.")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return 0

    series, t0, worst = [], time.monotonic(), (-1.0, None)
    while time.monotonic() - t0 < a.seconds:
        buf = grab(a.scale)
        if buf is None:
            continue
        got = parse_ppm(buf)
        if not got:
            continue
        w, h, px = got
        band, frac, colour = flat_rows(w, h, px, a.top, a.bottom, a.col_step, a.tol)
        series.append({"t": round(time.monotonic() - t0, 3), "band": round(band, 4),
                       "flat": round(frac, 4),
                       "colour": "#%02x%02x%02x" % colour if colour else None})
        if a.save_worst and band > worst[0]:
            worst = (band, buf)
    if not series:
        print("blankwatch: 0 samples -- grim produced no frame. The screen may be off "
              "(ph-ui.py unblank), or its content is on a hardware plane that "
              "screencopy cannot read.", file=sys.stderr)
        return 0

    if a.save_worst and worst[1] is not None:
        # Convert with whatever is on the device; a PPM is not viewable off it.
        try:
            subprocess.run(["magick", "ppm:-", a.save_worst], input=worst[1],
                           capture_output=True, timeout=30, check=True)
        except Exception:
            with open(a.save_worst + ".ppm", "wb") as f:
                f.write(worst[1])
            print("saved raw PPM (no `magick` on this device): %s.ppm" % a.save_worst,
                  file=sys.stderr)

    bd = [s["band"] for s in series]
    fl = [s["flat"] for s in series]
    summary = {"samples": len(series), "rate_hz": round(len(series) / a.seconds, 1),
               "band_p50": round(sorted(bd)[len(bd) // 2], 3),
               "band_max": round(max(bd), 3),
               "band_over_15pct": sum(b > 0.15 for b in bd),
               "band_over_40pct": sum(b > 0.40 for b in bd),
               "flat_p50": round(sorted(fl)[len(fl) // 2], 3)}
    if a.json:
        print(json.dumps({"summary": summary, "series": series}))
    else:
        for s in series:
            print("t=%6.3f band=%.3f flat=%.3f %s"
                  % (s["t"], s["band"], s["flat"], s["colour"] or ""))
        print("blankwatch: " + " ".join("%s=%s" % kv for kv in summary.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
