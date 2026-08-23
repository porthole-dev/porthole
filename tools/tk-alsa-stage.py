#!/usr/bin/env python3
# scope: generic
"""Walk the ALSA PCM lifecycle one call at a time, marking /dev/kmsg before each.

Why this exists: opening the capture PCM hard-hangs the SoC with zero console
output (HANDOFF-audio.md 5), so the usual "read the oops" move is unavailable.
`arecord` does open -> hw_params -> prepare -> start -> read as one opaque step,
so a hang only tells you "somewhere in there".

This drives libasound directly and writes a distinct marker to /dev/kmsg before
*each* call. With the host-side dmesg stream armed (tools/tk-stream.sh), the
last marker that reaches the host names the last call that started, and the
absent one names the call that never returned. That pins the failing stage in a
SINGLE run and with NO kernel rebuild.

Each marker is followed by a short sleep so the line is actually on the wire
before the dangerous call runs -- a marker still sitting in a buffer when the
SoC stops is a marker you never see.

Usage (as root, for /dev/kmsg):
    sudo python3 tk-alsa-stage.py [--device hw:0,1] [--playback] [--stop-after STAGE]

--stop-after lets you bisect by *not* running the fatal call, e.g.
`--stop-after prepare` exercises everything up to and including prepare and
then closes, which tells you whether teardown is survivable.
"""

import argparse
import ctypes
import ctypes.util
import os
import sys
import threading
import time

SND_PCM_STREAM_PLAYBACK = 0
SND_PCM_STREAM_CAPTURE = 1
SND_PCM_ACCESS_RW_INTERLEAVED = 3
SND_PCM_FORMAT_S16_LE = 2

STAGES = ["open", "hwp_any", "hwp_set", "hwp_install", "prepare", "start", "io", "close"]

_kmsg = None


def mark(tag):
    """Emit a marker to the kernel log and give it time to reach the host."""
    line = "### TKSTAGE %s\n" % tag
    # kmsg first, always: stdout is an ssh pipe that can block on backpressure,
    # and a marker that never left because of a stalled pipe is indistinguishable
    # from a marker that never left because the SoC died.
    err = None
    if _kmsg is not None:
        try:
            os.write(_kmsg, line.encode())
        except OSError as exc:
            err = exc
    sys.stdout.write(line if err is None else line.rstrip() + " (kmsg failed: %s)\n" % err)
    sys.stdout.flush()
    time.sleep(0.15)


def start_heartbeat(period_ms):
    """Tick into the kernel log from a side thread until the SoC stops.

    The staged markers say which call was entered; they cannot say *when* the
    machine died relative to it, and they cannot distinguish "the call blocked
    forever" from "the call returned and something asynchronous killed us a
    moment later". The last beat to reach the host timestamps the death, and
    whether a stage marker appears between two beats says which of those it was.
    """
    t0 = time.monotonic()
    n = [0]

    def beat():
        while True:
            n[0] += 1
            ms = (time.monotonic() - t0) * 1000.0
            line = "### TKBEAT %d t=%.0fms\n" % (n[0], ms)
            if _kmsg is not None:
                try:
                    os.write(_kmsg, line.encode())
                except OSError:
                    return
            time.sleep(period_ms / 1000.0)

    t = threading.Thread(target=beat, daemon=True)
    t.start()
    return t


def check(rc, what):
    if rc < 0:
        raise RuntimeError("%s failed: %d (%s)" % (what, rc, snd_strerror(rc)))
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="hw:0,1")
    ap.add_argument("--playback", action="store_true")
    ap.add_argument("--rate", type=int, default=48000)
    ap.add_argument("--channels", type=int, default=1)
    ap.add_argument("--frames", type=int, default=4800)
    ap.add_argument("--periods", type=int, default=None,
                    help="force the period count (one q6asm_read is queued per period)")
    ap.add_argument("--heartbeat", type=float, default=0,
                    help="tick the kernel log every N ms from a side thread")
    ap.add_argument("--stop-after", choices=STAGES, default=None,
                    help="run up to and including this stage, then close")
    args = ap.parse_args()

    global _kmsg, snd_strerror
    # /dev/kmsg writes from userspace are rate limited to 10 messages per 5s by
    # default (kernel.printk_devkmsg = "ratelimit"). That silently swallows
    # every marker past the tenth, which looks EXACTLY like the machine hanging
    # mid-walk -- it cost one wrong conclusion already. Turn it off, loudly.
    try:
        with open("/proc/sys/kernel/printk_devkmsg", "r+") as f:
            before = f.read().strip()
            if before != "on":
                f.seek(0)
                f.write("on\n")
        sys.stdout.write("printk_devkmsg: %s -> on\n" % before)
    except OSError as exc:
        sys.stderr.write("WARNING: could not disable /dev/kmsg ratelimit (%s).\n"
                         "         Markers past the 10th will be DROPPED -- do not read\n"
                         "         a missing marker as a hang.\n" % exc)

    try:
        _kmsg = os.open("/dev/kmsg", os.O_WRONLY)
    except OSError as exc:
        sys.stderr.write("warning: no /dev/kmsg (%s); markers go to stdout only\n" % exc)

    lib = ctypes.CDLL(ctypes.util.find_library("asound") or "libasound.so.2")

    lib.snd_strerror.restype = ctypes.c_char_p
    _strerror = lib.snd_strerror

    def snd_strerror(rc):
        return _strerror(rc).decode()

    stream = SND_PCM_STREAM_PLAYBACK if args.playback else SND_PCM_STREAM_CAPTURE
    handle = ctypes.c_void_p()
    params = ctypes.c_void_p()

    def done_after(stage):
        return args.stop_after == stage

    if args.heartbeat:
        start_heartbeat(args.heartbeat)

    mark("S0 begin device=%s stream=%s" % (args.device, "playback" if args.playback else "capture"))

    mark("S1 open ->")
    check(lib.snd_pcm_open(ctypes.byref(handle), args.device.encode(), stream, 0), "snd_pcm_open")
    mark("S1 open OK")
    if done_after("open"):
        return finish(lib, handle)

    check(lib.snd_pcm_hw_params_malloc(ctypes.byref(params)), "hw_params_malloc")

    mark("S2 hw_params_any ->")
    check(lib.snd_pcm_hw_params_any(handle, params), "hw_params_any")
    mark("S2 hw_params_any OK")
    if done_after("hwp_any"):
        return finish(lib, handle)

    mark("S3 hw_params_set_* ->")
    check(lib.snd_pcm_hw_params_set_access(handle, params, SND_PCM_ACCESS_RW_INTERLEAVED),
          "set_access")
    check(lib.snd_pcm_hw_params_set_format(handle, params, SND_PCM_FORMAT_S16_LE), "set_format")
    check(lib.snd_pcm_hw_params_set_channels(handle, params, args.channels), "set_channels")
    rate = ctypes.c_uint(args.rate)
    check(lib.snd_pcm_hw_params_set_rate_near(handle, params, ctypes.byref(rate), None),
          "set_rate_near")
    periods = args.periods
    if periods:
        nper = ctypes.c_uint(periods)
        check(lib.snd_pcm_hw_params_set_periods_near(handle, params, ctypes.byref(nper), None),
              "set_periods_near")
        periods = nper.value
    mark("S3 hw_params_set_* OK rate=%d periods=%s" % (rate.value, periods))
    if done_after("hwp_set"):
        return finish(lib, handle)

    # The install is the first call that reaches the DSP/SLIMbus backend: ASoC
    # runs the whole DPCM backend hw_params from here.
    mark("S4 hw_params INSTALL ->")
    check(lib.snd_pcm_hw_params(handle, params), "snd_pcm_hw_params")
    mark("S4 hw_params INSTALL OK")
    if done_after("hwp_install"):
        return finish(lib, handle)

    mark("S5 prepare ->")
    check(lib.snd_pcm_prepare(handle), "snd_pcm_prepare")
    mark("S5 prepare OK")
    if done_after("prepare"):
        return finish(lib, handle)

    lib.snd_pcm_readi.restype = ctypes.c_long
    lib.snd_pcm_writei.restype = ctypes.c_long
    buf = ctypes.create_string_buffer(args.frames * args.channels * 2)

    # Playback needs data in the ring BEFORE the stream is started: calling
    # snd_pcm_start() on an empty playback buffer is refused by the ALSA core
    # with -EPIPE and never reaches the driver's trigger, so ASM RUN is never
    # sent -- and "it survived" would then mean nothing at all. Capture is the
    # opposite: it must be started before there is anything to read.
    if args.playback:
        mark("S7 writei %d frames (pre-start fill) ->" % args.frames)
        rc = lib.snd_pcm_writei(handle, buf, ctypes.c_ulong(args.frames))
        mark("S7 writei returned %d" % rc)

    mark("S6 start ->")
    check(lib.snd_pcm_start(handle), "snd_pcm_start")
    mark("S6 start OK")
    if done_after("start"):
        return finish(lib, handle)

    if not args.playback:
        mark("S7 readi %d frames ->" % args.frames)
        rc = lib.snd_pcm_readi(handle, buf, ctypes.c_ulong(args.frames))
        mark("S7 readi returned %d" % rc)

    return finish(lib, handle)


def finish(lib, handle):
    mark("S8 close ->")
    lib.snd_pcm_close(handle)
    mark("S8 close OK")
    mark("S9 DONE-ALL-STAGES-SURVIVED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        mark("ERR %s" % exc)
        sys.exit(1)
