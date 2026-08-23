#!/usr/bin/env python3
# scope: generic
"""Measure the capture gating (HANDOFF-audio.md 2.5) as a number, per config.

The section-4 acceptance rule cannot see a 2:1, and neither can a spectrum on
its own. What characterises this defect is the *shape* of the loss: N samples
delivered, then M slots empty, at a fixed period. So report the period, the
duty, and the real sample rate -- those are what a fix has to move.

    tk-gap.py sweep snd_soc_wcd934x slim_watermark 0 1 2 3
    tk-gap.py once [tag]

Each trial reboots the phone: a module reload re-registers the card but
capture then fails at hw_params until a reboot (2.5a).
"""
import os
import subprocess
import struct
import sys
import time
import wave

# Locate lib/porthole.py by walking up, so this works both from tools/ and
# from profiles/<device>/tools/ without either hardcoding a depth.
import pathlib as _pl, sys as _sys
for _p in _pl.Path(__file__).resolve().parents:
    if (_p / "lib" / "porthole.py").is_file():
        _sys.path.insert(0, str(_p / "lib"))
        break
import porthole

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "logs", "gap")
PHONE = porthole.resolve_phone(porthole.load_config())
SSH = ["ssh", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=no",
       "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-o", "BatchMode=yes"]

RECIPE = [
    "MultiMedia2 Mixer SLIMBUS_0_TX=1",
    "AIF1_CAP Mixer SLIM TX7=1",
    "CDC_IF TX7 MUX=DEC7",
    "ADC MUX7=DMIC",
    "DMIC MUX7=DMIC0",
]


def sh(cmd, timeout=180):
    return subprocess.run(SSH + [PHONE, cmd], capture_output=True, text=True,
                          timeout=timeout)


def boot_id():
    return sh("cat /proc/sys/kernel/random/boot_id").stdout.strip()


def reboot():
    old = boot_id()
    sh("sudo -n systemctl reboot")
    for _ in range(120):
        time.sleep(2)
        new = boot_id()
        if new and new != old:
            return True
    raise SystemExit("phone did not come back")


def capture(dur=12):
    sh("sudo systemctl stop greetd >/dev/null 2>&1; "
       "for p in pipewire pipewire-pulse wireplumber pulseaudio arecord aplay; do "
       "sudo pkill -9 -x $p; done; sleep 1")
    # every AIF1_CAP switch cleared first: one left on by an earlier experiment
    # silently makes a 1-channel capture 2-channel
    sh("for n in $(seq 0 15); do amixer -c0 -q cset name=\"AIF1_CAP Mixer SLIM TX$n\" 0; done")
    sh("; ".join(f"amixer -c0 -q cset name='{c.split('=')[0]}' '{c.split('=')[1]}'"
                 for c in RECIPE))
    sh(f"arecord -D hw:0,1 -f S16_LE -r 48000 -c 1 -d {dur} $HOME/gap.wav "
       f">/tmp/gap.log 2>&1; true", timeout=dur + 90)
    os.makedirs(OUT, exist_ok=True)
    local = os.path.join(OUT, "gap.wav")
    subprocess.run(["scp", "-q"] + SSH[1:] + [f"{PHONE}:$HOME/gap.wav", local],
                   check=False)
    return local


def analyse(path, at=5.0):
    with wave.open(path) as w:
        raw = w.readframes(w.getnframes())
    x = struct.unpack(f"<{len(raw)//2}h", raw)
    i = int(at * 48000)
    seg = x[i:i + 48000]
    if len(seg) < 48000:
        return dict(ok=False, note=f"only {len(x)/48000:.1f}s captured")
    zeros = sum(1 for v in seg if v == 0)
    chg = sum(1 for k in range(1, len(seg)) if seg[k] != seg[k - 1])
    # Smallest period with at least one phase that is zero in EVERY group --
    # a phase that is never written is the gap, and it is unambiguous even
    # when the live phases happen to cross zero.
    period = dead = None
    for p in range(2, 33):
        groups = len(seg) // p
        ph = [sum(1 for k in range(off, groups * p, p) if seg[k] == 0)
              for off in range(p)]
        d = sum(1 for c in ph if c == groups)
        if d and d < p:
            period, dead = p, d
            break
    real = 48000 * (period - dead) / period if period else 48000 - zeros
    return dict(ok=True, zeros=100 * zeros / len(seg), chg=chg, period=period,
                dead=dead, real=real)


def report(tag, r):
    if not r["ok"]:
        print(f"  {tag:<24} FAILED: {r['note']}")
        return
    shape = (f"{r['period'] - r['dead']} on / {r['dead']} off, period {r['period']}"
             if r["period"] else "no fixed gap period")
    print(f"  {tag:<24} zeros={r['zeros']:5.1f}%  chg/s={r['chg']:6d}  "
          f"real={r['real']:6.0f}/s  {shape}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "sweep":
        mod, param, *values = sys.argv[2:]
        print(f"== sweeping {mod}.{param} (a reboot per value -- 2.5a)")
        for v in values:
            sh(f"echo 'options {mod} {param}={v}' | "
               f"sudo tee /etc/modprobe.d/tk-gap.conf >/dev/null")
            reboot()
            got = sh(f"cat /sys/module/{mod}/parameters/{param}").stdout.strip()
            report(f"{param}={v} (is {got})", analyse(capture()))
        sh("sudo rm -f /etc/modprobe.d/tk-gap.conf")
        print("  (removed /etc/modprobe.d/tk-gap.conf; reboot to return to defaults)")
    else:
        report(sys.argv[1] if len(sys.argv) > 1 else "now", analyse(capture()))


if __name__ == "__main__":
    main()
