#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: BOOTED
# env: PHONE, TK_AMP
# exits: 0 ok · non-zero on failure
"""tk-acoustic -- decide "does audio actually come out / go in" with no human.

The laptop has a speaker and a microphone and the phone sits next to it, so
both directions are a closed acoustic loop:

  tk-acoustic.py speaker      phone plays a tone, THIS MACHINE records it
  tk-acoustic.py mic          this machine plays a tone, the PHONE records it
  tk-acoustic.py selftest     the phone plays AND listens with its own mic

Both end in the same verdict: a Goertzel filter at the played frequency
against the off-tone noise floor.  That is the rule that matters -- section 4's
AC/distinct test passes on room noise and told us nothing (HANDOFF-audio.md
2.5b), while "is the tone I played 12 dB above everything else" cannot be
faked by a settling transient, a latched port or a silent room.

The phone side of the mic direction is tk-lab's capture(), reused verbatim so
the mixer discipline (reset, set once, never touch mid-stream) still holds.
"""
import argparse
import importlib.util
import math
import os
import struct
import subprocess
import sys
import time
import wave

# Resolve config through the shared lib: this is what supplies $PHONE, the
# mandatory ssh flags (host keys change every boot) and connection
# multiplexing. A tool that builds its own ssh command line gets none of them.
import pathlib as _pl, sys as _sys
for _p in _pl.Path(__file__).resolve().parents:
    if (_p / "lib" / "porthole.py").is_file():
        _sys.path.insert(0, str(_p / "lib"))
        break
import porthole

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "logs", "acoustic")
RATE = 48000
AMP = float(os.environ.get("TK_AMP", 0.1))   # phone-side playback amplitude.
# Full scale on both amps browns the phone out and it silently REBOOTS
# mid-test, which reads as an intermittent speaker. 0.1 is still ~50 dB above
# the phone mic's own floor.
PASS_DB = 20.0   # tone-vs-floor margin that counts as audible; the host
                 # speaker-to-host mic loop measures +73 dB, and pure room
                 # noise has produced +10, so 12 was too close to the noise.

_spec = importlib.util.spec_from_file_location("tk_lab", os.path.join(HERE, "tk-lab.py"))
tk_lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tk_lab)


# ----------------------------------------------------------------- analysis

def goertzel(x, f, rate=RATE):
    """Magnitude of x at frequency f.  One bin, no numpy, O(n)."""
    n = len(x)
    k = round(n * f / rate)
    w = 2 * math.pi * k / n
    coeff, cw, sw = 2 * math.cos(w), math.cos(w), math.sin(w)
    s1 = s2 = 0.0
    for v in x:
        s1, s2 = v + coeff * s1 - s2, s1
    return math.hypot(s1 - s2 * cw, s2 * sw) * 2 / n


def read_wav(path):
    with wave.open(path) as w:
        if w.getsampwidth() != 2:
            sys.exit(f"{path}: expected 16-bit, got {w.getsampwidth()*8}")
        ch, rate = w.getnchannels(), w.getframerate()
        raw = w.readframes(w.getnframes())
    s = struct.unpack(f"<{len(raw)//2}h", raw)
    return list(s[::ch]), rate      # first channel only


def verdict(path, f, skip=2.5):
    """Is `f` present, and how far above the floor?  Returns (ok, db, detail)."""
    x, rate = read_wav(path)
    x = x[int(skip * rate):]
    if len(x) < rate:                       # under a second of usable audio
        return False, 0.0, f"only {len(x)/rate:.1f}s after the {skip}s skip"
    # Off-tone probes deliberately avoid harmonics and the 8k/16k gating images
    # of the capture defect (2.5b), which are real signal and would mask a
    # genuine floor.
    floor = sorted(goertzel(x, f * m, rate) for m in (0.61, 0.79, 1.31, 1.73))
    mag = goertzel(x, f, rate)
    med = (floor[1] + floor[2]) / 2 or 1e-9
    db = 20 * math.log10(mag / med) if mag > 0 else -99.0
    peak = max(abs(v) for v in x)
    return db >= PASS_DB, db, f"mag={mag:.1f} floor={med:.1f} peak={peak}"


# -------------------------------------------------------------------- tones

def write_tone(path, f, dur, amp=0.5):
    n = int(dur * RATE)
    frames = b"".join(struct.pack("<h", int(32767 * amp * math.sin(2 * math.pi * f * t / RATE)))
                      for t in range(n))
    with wave.open(path, "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(frames)
    return path


def write_tone_stereo(path, f, dur, amp=0.1):
    """The QUAT MI2S backend is pinned to stereo S16_LE, so the phone-side file
    has to be stereo or aplay resamples/refuses."""
    n = int(dur * RATE)
    frames = b"".join(struct.pack("<hh", *([int(32767 * amp * math.sin(2 * math.pi * f * t / RATE))] * 2))
                      for t in range(n))
    with wave.open(path, "w") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(frames)
    return path


# ---------------------------------------------------------------- direction

def host_quiet():
    """Keep it civil: the phone is next to the laptop and it is late."""
    subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "25%"], check=False)
    subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"], check=False)
    subprocess.run(["pactl", "set-source-mute", "@DEFAULT_SOURCE@", "0"], check=False)
    subprocess.run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", "100%"], check=False)


def do_speaker(f, dur, mixer):
    """Phone plays, laptop records."""
    os.makedirs(OUT, exist_ok=True)
    local_tone = write_tone_stereo(os.path.join(OUT, "tone-tx.wav"), f, dur, amp=AMP)
    heard = os.path.join(OUT, "heard.wav")
    host_quiet()

    # busybox pkill takes ONE pattern, so these must be separate calls.
    tk_lab.sh("sudo systemctl stop greetd >/dev/null 2>&1; "
              "for p in pipewire pipewire-pulse wireplumber pulseaudio aplay; do "
              "sudo pkill -9 -x $p; done; sleep 1; sudo dmesg -C")
    subprocess.run(["scp", "-q"] + tk_lab.SSH[1:] + [local_tone, f"{tk_lab.PHONE}:$HOME/tone-tx.wav"],
                   check=True)
    for ctl in mixer:
        r = tk_lab.sh(f"amixer -c0 -q cset name='{ctl.split('=')[0]}' '{ctl.split('=')[1]}'")
        if r.returncode:
            print(f"  WARN mixer {ctl}: {r.stderr.strip()}")

    # Record FIRST -- parecord takes a moment to open the source, and a tone
    # that starts before the recorder does is a false negative.
    rec = subprocess.Popen(["parecord", "--channels=1", f"--rate={RATE}",
                            "--file-format=wav", heard],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    print(f"  phone: aplay {dur}s @ {f} Hz")
    play = tk_lab.sh(f"timeout {dur + 10} aplay -D hw:0,0 $HOME/tone-tx.wav 2>&1")
    time.sleep(1.0)
    rec.terminate(); rec.wait()

    print("  aplay:", (play.stdout or play.stderr).strip().replace("\n", " | ") or "(silent)")
    return heard


def do_selftest(f, dur, sets):
    """Phone plays and the PHONE's own mic listens.

    This is the sensitive test, not the laptop one: measured on this setup the
    phone hears a tone from the laptop speaker across the desk at +61 dB, while
    the laptop hears the phone at the noise floor.  A speaker centimetres from
    that mic cannot be quiet by accident.
    """
    os.makedirs(OUT, exist_ok=True)
    write_tone_stereo(os.path.join(OUT, "tone-tx.wav"), f, dur, amp=AMP)
    subprocess.run(["scp", "-q"] + tk_lab.SSH[1:] + [os.path.join(OUT, "tone-tx.wav"),
                   f"{tk_lab.PHONE}:$HOME/tone-tx.wav"], check=True)
    tk_lab.sh("sudo systemctl stop greetd >/dev/null 2>&1; "
              "for p in pipewire pipewire-pulse wireplumber pulseaudio aplay arecord; do "
              "sudo pkill -9 -x $p; done; sleep 1; sudo dmesg -C")
    tk_lab.reset_mixer()
    tk_lab.set_mixer(list(sets) + ["QUAT_MI2S_RX Audio Mixer MultiMedia1=1"])
    # Capture first, then play into it -- same reason as the host direction.
    tk_lab.sh(f"(arecord -D hw:0,1 -f S16_LE -r 48000 -c 1 -d {dur + 4} "
              f"$HOME/selftest.wav >/tmp/ar.log 2>&1 &); sleep 2; "
              f"(timeout {dur + 6} aplay -D hw:0,0 $HOME/tone-tx.wav >/tmp/ap.log 2>&1 &); "
              f"while pgrep -x arecord >/dev/null; do sleep 1; done")
    local = os.path.join(OUT, "selftest.wav")
    subprocess.run(["scp", "-q"] + tk_lab.SSH[1:] +
                   [f"{tk_lab.PHONE}:$HOME/selftest.wav", local], check=False)
    print("  aplay:", tk_lab.sh("cat /tmp/ap.log").stdout.strip().replace("\n", " | "))
    return local


def do_mic(f, dur, sets):
    """Laptop plays, phone records.  The tone runs for the whole capture,
    including tk-lab's several seconds of mixer setup, so no alignment is
    needed -- see the method note in HANDOFF-audio.md 2.5b."""
    os.makedirs(OUT, exist_ok=True)
    tone = write_tone(os.path.join(OUT, "tone-rx.wav"), f, dur + 40)
    host_quiet()
    play = subprocess.Popen(["paplay", tone], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        r = tk_lab.capture("acoustic", sets, dur=dur, quiet=True)
        print(f"  tk-lab: dc={r['dc']:.1f} ac={r['ac']:.2f} distinct={r['distinct']} "
              f"live={r['live']}/{r['secs']}")
    finally:
        play.terminate(); play.wait()
    return os.path.join(tk_lab.LAB, "acoustic", "cap.wav")


def host_record(path, secs):
    """Record on this machine with nothing else running -- the control arm."""
    rec = subprocess.Popen(["parecord", "--channels=1", f"--rate={RATE}",
                            "--file-format=wav", path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(secs)
    rec.terminate(); rec.wait()
    return path


def trials(kind, f, dur, sets, n):
    """Alternate real trials with silent controls.

    One trial decides nothing here: a 1 kHz room has produced +14 dB of pure
    noise and a genuine tone from the host speaker measures +73 dB.  So run
    both arms n times and require every tone trial to beat every control.
    """
    tone_db, ctl_db = [], []
    for i in range(n):
        wav = do_speaker(f, dur, sets or ["QUAT_MI2S_RX Audio Mixer MultiMedia1=1"]) \
            if kind == "speaker" else do_mic(f, dur, sets or (tk_lab.BASE_TX7 + ["DMIC MUX7=DMIC0"]))
        tone_db.append(verdict(wav, f)[1])
        if kind == "speaker":       # control: same recorder, phone silent
            ctl_db.append(verdict(host_record(os.path.join(OUT, "ctl.wav"), dur + 2), f)[1])
        print(f"  trial {i+1}: tone {tone_db[-1]:+6.1f} dB"
              + (f"   control {ctl_db[-1]:+6.1f} dB" if ctl_db else ""))
    lo = min(tone_db)
    ok = lo >= PASS_DB and (not ctl_db or lo > max(ctl_db))
    print(f"\n{'AUDIBLE' if ok else 'NOT AUDIBLE'}  {f:.0f} Hz  "
          f"tone {min(tone_db):+.1f}..{max(tone_db):+.1f} dB"
          + (f"   control {min(ctl_db):+.1f}..{max(ctl_db):+.1f} dB" if ctl_db else ""))
    return ok


def main():
    p = argparse.ArgumentParser(prog="tk-acoustic.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("speaker", "mic", "selftest"):
        s = sub.add_parser(name)
        s.add_argument("-f", "--freq", type=float, default=1234.0)
        s.add_argument("-d", "--dur", type=int, default=20 if name == "mic" else 10)
        s.add_argument("-s", "--set", action="append", default=[])
        s.add_argument("-n", "--trials", type=int, default=0,
                       help="paired trials against a silent control; 0 = single shot")
    s = sub.add_parser("verdict"); s.add_argument("wav"); s.add_argument("-f", "--freq", type=float, default=1000.0)
    a = p.parse_args()

    if a.cmd != "verdict" and a.trials:
        sys.exit(0 if trials(a.cmd, a.freq, a.dur, a.set, a.trials) else 1)

    if a.cmd == "verdict":
        wav = a.wav
    elif a.cmd == "speaker":
        wav = do_speaker(a.freq, a.dur,
                         a.set or ["QUAT_MI2S_RX Audio Mixer MultiMedia1=1"])
    elif a.cmd == "selftest":
        wav = do_selftest(a.freq, a.dur,
                          a.set or (tk_lab.BASE_TX7 + ["DMIC MUX7=DMIC0"]))
    else:
        wav = do_mic(a.freq, a.dur,
                     a.set or (tk_lab.BASE_TX7 + ["DMIC MUX7=DMIC0"]))

    ok, db, detail = verdict(wav, a.freq)
    print(f"\n{'HEARD' if ok else 'NOT HEARD'}  {a.freq:.0f} Hz  {db:+.1f} dB over floor "
          f"(need {PASS_DB:+.0f})  [{detail}]\n  {wav}")
    sys.exit(0 if ok else 1)


def demo():
    """Self-check: the verdict must fire on a tone and not on noise."""
    import random
    os.makedirs("/tmp/tk-ac", exist_ok=True)
    good = write_tone("/tmp/tk-ac/g.wav", 1000, 6, amp=0.2)
    assert verdict(good, 1000)[0], "clean tone must pass"
    with wave.open("/tmp/tk-ac/n.wav", "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", random.randint(-3000, 3000))
                               for _ in range(RATE * 6)))
    assert not verdict("/tmp/tk-ac/n.wav", 1000)[0], "white noise must fail"
    with wave.open("/tmp/tk-ac/s.wav", "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(b"\0\0" * (RATE * 6))
    assert not verdict("/tmp/tk-ac/s.wav", 1000)[0], "silence must fail"
    print("demo ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        demo()
    else:
        main()
