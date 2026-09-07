#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: device:google-taimen
# needs: BOOTED (runs ON the device)
# env: -
# exits: 0 ok · 1 could not open a stream
# lib-exempt: runs on the device, not the host -- there is no ph-lib.sh there.
"""Hold the voice PCM open for the duration of a call.

This is the counterpart of what Android's audio HAL does, and it is needed for
the same reason. A voice call carries no samples across the application
processor: the modem exchanges speech with the ADSP and the ADSP drives the
codec. But in this stack only an open PCM powers an AFE port, so something has
to open the voice front-end and leave it open -- downstream's HAL opens both
directions explicitly, and q6voice starts the session once both have prepared
(msm-pcm-voice-v2.c: `if (prtd->playback_start && prtd->capture_start)`).

PipeWire will not do it: it creates the sink and source for the Voice Call
profile but leaves them suspended, because nothing is streaming to them.

Opening is all that is required -- NOT transferring. Downstream's voice PCM
defines no .pointer and no .copy at all, so aplay and arecord cannot work on it
by design, and this deliberately does not try.
"""
import ctypes
import signal
import sys
import time

SND_PCM_STREAM_PLAYBACK = 0
SND_PCM_STREAM_CAPTURE = 1
SND_PCM_FORMAT_S16_LE = 2
SND_PCM_ACCESS_RW_INTERLEAVED = 3

asound = ctypes.CDLL("libasound.so.2")


def _check(rc, what):
    if rc < 0:
        asound.snd_strerror.restype = ctypes.c_char_p
        raise OSError(f"{what}: {asound.snd_strerror(rc).decode()}")
    return rc


def open_stream(device, direction, rate=48000, channels=1):
    """Open, configure and prepare one direction. Never transfers."""
    handle = ctypes.c_void_p()
    _check(asound.snd_pcm_open(ctypes.byref(handle), device.encode(),
                               direction, 0), f"open {device}")
    _check(asound.snd_pcm_set_params(handle, SND_PCM_FORMAT_S16_LE,
                                     SND_PCM_ACCESS_RW_INTERLEAVED,
                                     channels, rate, 1, 500000),
           "set_params")
    _check(asound.snd_pcm_prepare(handle), "prepare")

    return handle


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else "hw:0,2"

    # A direction the sound server already holds is not a failure: it is
    # already open and prepared, which is the whole point. Measured on a live
    # call -- callaudiod selects the Voice Call profile and PipeWire opens the
    # SINK for it, while the source stays suspended because nothing records.
    # So in practice this ends up holding only the capture side, which is
    # exactly the half that was missing.
    handles = []
    held = []
    for name, direction in (("playback", SND_PCM_STREAM_PLAYBACK),
                            ("capture", SND_PCM_STREAM_CAPTURE)):
        try:
            handles.append(open_stream(device, direction))
            held.append(name)
            print(f"{name}: opened here", flush=True)
        except OSError as exc:
            if "busy" in str(exc).lower():
                held.append(name)
                print(f"{name}: already open (the sound server holds it)",
                      flush=True)
                continue
            print(f"{name} FAILED: {exc}", flush=True)

    if len(held) != 2:
        print("only %s is up -- the session needs both directions"
              % ", ".join(held or ["nothing"]), flush=True)
        for h in handles:
            asound.snd_pcm_close(h)
        return 1

    print("both directions held -- q6voice should have started the session",
          flush=True)

    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    while not stop:
        time.sleep(0.5)

    for h in handles:
        asound.snd_pcm_close(h)
    print("released", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
