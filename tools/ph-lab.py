#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: soc:msm8998
# needs: BOOTED
# env: FASTBOOT, PHONE, TK_LAB_ALLOW_VOL
# exits: 0 ok
"""tk-lab -- unattended audio experiment harness for taimen.

Every audio hypothesis on this device used to cost a kernel build.  This runs
the whole loop from the host instead: set a mixer recipe, record, snapshot the
codec registers *during* the stream, pull everything back, and apply the
docs/HANDOFF-audio.md section 4 acceptance rule.  A sweep of a dozen recipes
runs unattended in a few minutes.

  tk-lab.py snap  TAG                 state snapshot (regs + dapm + mixer + irq)
  tk-lab.py diff  TAG_A TAG_B         symbolic register diff
  tk-lab.py show  REG...              symbolic read (name or 0xADDR), live
  tk-lab.py poke  REG VAL             live register write (needs write-debugfs)
  tk-lab.py cap   NAME [-d SEC] [-s 'Ctl=Val']...
  tk-lab.py sweep NAME [-d SEC]       run a named recipe group from RECIPES
  tk-lab.py verdict FILE.wav          re-analyse a pulled capture

Rules this encodes, all learned expensively (see docs/HANDOFF-audio.md):
  - the mixer is set ONCE before the stream and never touched during it
  - the first 2.5s are discarded; a mic passes only on sustained AC in EVERY
    second of a 20s+ capture
  - register readback goes through cache_bypass or it reports what the driver
    *believes* it wrote
  - 'DECn Volume' is never written unless you ask for it explicitly: the
    kcontrol max (40) is below the register default (84), so a write is a large
    gain cut you cannot undo without a reboot
"""
import argparse
import gzip
import math
import os
import re
import subprocess
import sys
import wave
from cmath import exp, pi

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
LAB = os.path.join(ROOT, "logs", "lab")
HDR = os.path.join(ROOT, "linux", "include", "linux", "mfd", "wcd934x", "registers.h")

PHONE = porthole.resolve_phone(porthole.load_config())
SSH = ["ssh", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=no",
       "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-o", "BatchMode=yes"]

# The two SLIMbus regmaps.  1:0 is the codec proper, 0:0 is the interface
# device where the slave port config lives.  Neither is writable without
# CONFIG_REGMAP_ALLOW_WRITE_DEBUGFS.
RM_CODEC = "217:250:1:0"
RM_IFD = "217:250:0:0"


def sh(cmd, **kw):
    return subprocess.run(SSH + [PHONE, cmd], capture_output=True, text=True, **kw)


def shb(cmd, timeout=180):
    """Run on the phone, return raw bytes (for gzipped payloads)."""
    return subprocess.run(SSH + [PHONE, cmd], capture_output=True, timeout=timeout).stdout


# ------------------------------------------------------------------ registers

def regnames():
    """{addr: NAME} from the mainline wcd934x register header."""
    out = {}
    with open(HDR) as f:
        for name, val in re.findall(r"#define\s+(WCD934X_\w+)\s+(0x[0-9a-fA-F]{3,4})\b", f.read()):
            out.setdefault(int(val, 16), name[len("WCD934X_"):])
    return out


def regaddrs():
    return {n: a for a, n in regnames().items()}


def resolve(tok):
    """'0x0d82' | 'CDC_TOP_TOP_CFG1' | 'WCD934X_CDC_TOP_TOP_CFG1' -> int addr."""
    tok = tok.strip()
    if re.fullmatch(r"(0x)?[0-9a-fA-F]{1,4}", tok) and (tok.startswith("0x") or tok.isdigit()):
        return int(tok, 16 if tok.startswith("0x") else 10)
    name = tok[len("WCD934X_"):] if tok.startswith("WCD934X_") else tok
    a = regaddrs().get(name)
    if a is None:
        sys.exit(f"unknown register: {tok}")
    return a


def parse_dump(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r"([0-9a-f]{4}):\s*([0-9a-f]{2})\s*$", line)
        if m:
            out[int(m.group(1), 16)] = int(m.group(2), 16)
    return out


def pull_regs(which=RM_CODEC, full=False, pages=None):
    """Cache-bypassed dump.

    The debugfs `registers` file is fixed-width -- exactly 9 bytes per line
    ("0d82: 03\\n") -- so `dd bs=9 skip=<addr>` seeks straight to a register.
    Dumping the whole 16-bit space costs ~30s per regmap; reading only the
    256-byte pages that contain a register the driver actually names costs
    well under a second, which is what makes a *mid-stream* snapshot possible
    at all.  Pass full=True when hunting for something unnamed.
    """
    f = f"/sys/kernel/debug/regmap/{which}/registers"
    pre = f"echo Y | sudo tee /sys/kernel/debug/regmap/{which}/cache_bypass >/dev/null; "
    if full:
        cmd = pre + f"sudo cat {f} | gzip -1"
    else:
        pages = sorted(pages if pages is not None else {a >> 8 for a in regnames()})
        reads = "; ".join(f"sudo dd if={f} bs=9 skip={p * 256} count=256 2>/dev/null" for p in pages)
        cmd = pre + f"{{ {reads}; }} | gzip -1"
    raw = shb(cmd)
    txt = gzip.decompress(raw).decode() if raw else ""
    # Fail loudly. Every dd in the pipeline is 2>/dev/null, so a wrong regmap
    # name or a path that is a directory rather than .../registers comes back
    # as an empty string -- which parse_dump() turns into {} and every caller
    # reads as "nothing is set". A blank dump is a broken instrument, never a
    # measurement. This has cost a session already; so has tk-fps.py reading a
    # nonexistent encoder and printing 0 fps all evening.
    if not parse_dump(txt):
        sys.exit(f"tk-lab: regmap '{which}' returned no registers -- check that "
                 f"/sys/kernel/debug/regmap/{which}/registers exists and is "
                 f"readable by sudo. NOT a measurement.")
    return txt


# ------------------------------------------------------------------ snapshots

STATE_CMDS = {
    "mixer.txt": "amixer -c0 contents",
    "dapm.txt": ("for w in /sys/kernel/debug/asoc/*/*/dapm/*; do "
                 "printf '%s: ' \"$(basename $w)\"; sudo head -1 $w 2>/dev/null; done"),
    "irq.txt": "grep -Ei 'slim|wcd|lpass|q6' /proc/interrupts",
    "dmesg.txt": "sudo dmesg | tail -400",
    "pcm.txt": "cat /proc/asound/card0/pcm*/sub0/status 2>/dev/null; cat /proc/asound/card0/pcm*/sub0/hw_params 2>/dev/null",
    # The SLIM interface device carries the slave port state: which ports are
    # grouped into one stream (MULTI_CHNL), the watermark/enable (PORT_CFG),
    # and the overflow/underflow interrupt enables the handler masks on first
    # error.  Once masked, "no underflow in the log" means nothing.
    "slim.txt": ("I=/sys/kernel/debug/regmap/217:250:0:0/registers; "
                 "for r in 48 49 50 51 52 53 54 55 80 81 82 83 84 85 86 87 88 "
                 "260 264 268 272 276 280 284 288; do "
                 "sudo dd if=$I bs=9 skip=$r count=1 2>/dev/null; done"),
}


def slim_errors(tag):
    """Port errors seen during a run.  BIT0 overflow, BIT1 underflow, BIT2 closed."""
    p = os.path.join(LAB, tag, "dmesg.txt")
    if not os.path.exists(p):
        return []
    return re.findall(r"(overflow|underflow|Port Closed) error?\s*(?:on)?\s*(TX|RX) port (\d+), value (\w+)",
                      open(p).read())


def snap(tag, regs=True):
    d = os.path.join(LAB, tag)
    os.makedirs(d, exist_ok=True)
    if regs:
        # The interface device's registers are not in the codec header, so name
        # lookup cannot pick its pages; the slave port config all sits low.
        for label, dev, pg in (("regs-codec.txt", RM_CODEC, None),
                               ("regs-ifd.txt", RM_IFD, range(0, 8))):
            txt = pull_regs(dev, pages=pg)
            open(os.path.join(d, label), "w").write(txt)
            print(f"  {label}: {len(parse_dump(txt))} registers")
    for fn, cmd in STATE_CMDS.items():
        open(os.path.join(d, fn), "w").write(sh(cmd).stdout)
    print(f"snapshot -> {d}")
    return d


def load_snap(tag, which="regs-codec.txt"):
    p = os.path.join(LAB, tag, which)
    if not os.path.exists(p):
        sys.exit(f"no such snapshot: {p}")
    return parse_dump(open(p).read())


def cmd_diff(a, b, prefix=None):
    names, A, B = regnames(), load_snap(a), load_snap(b)
    # Intersect, never union. Snapshots can cover different address ranges (a
    # `--full` dump has 65536 entries, a paged one ~3500), and a union reports
    # every address the narrower snapshot simply did not read as a change --
    # 61959 "differences" between two identical idle states, which is exactly
    # the kind of output someone builds a theory on.
    common = set(A) & set(B)
    if len(common) < max(len(A), len(B)):
        print(f"  (comparing {len(common)} registers present in both; "
              f"{a} has {len(A)}, {b} has {len(B)})")
    n = 0
    for addr in sorted(common):
        va, vb = A[addr], B[addr]
        if va == vb:
            continue
        nm = names.get(addr, "?")
        if prefix and not nm.startswith(prefix):
            continue
        print(f"  {addr:04x} {nm:<44} {va:02x} -> {vb:02x}")
        n += 1
    print(f"{n} registers differ ({a} -> {b})" + (f" [filter {prefix}]" if prefix else ""))


# --------------------------------------------------------------- live poke ---

def can_write():
    return "0" != sh("test -w /sys/kernel/debug/regmap/%s/registers && echo 1 || echo 0"
                     % RM_CODEC).stdout.strip()


def cmd_show(toks):
    names = regnames()
    live = parse_dump(pull_regs())
    for t in toks:
        a = resolve(t)
        v = live.get(a)
        print(f"  {a:04x} {names.get(a,'?'):<44} "
              f"{'--' if v is None else f'{v:02x}  0b{v:08b}'}")


def poke(reg, val):
    """Write one codec register. Returns True on success.

    Needs CONFIG_REGMAP_ALLOW_WRITE_DEBUGFS=y; upstream ships no such option
    (it reaches PMIC registers too), so this is a debug-config-only escape
    hatch. It is what makes a register hypothesis cost one second instead of a
    kernel build.
    """
    a, v = resolve(reg), int(str(val), 0)
    r = sh(f"echo Y | sudo tee /sys/kernel/debug/regmap/{RM_CODEC}/cache_bypass >/dev/null; "
           f"printf '%x %x\\n' {a} {v} | sudo tee "
           f"/sys/kernel/debug/regmap/{RM_CODEC}/registers >/dev/null && echo ok")
    return "ok" in r.stdout


def cmd_poke(reg, val):
    if not poke(reg, val):
        sys.exit("write failed -- kernel needs CONFIG_REGMAP_ALLOW_WRITE_DEBUGFS=y")
    cmd_show([reg])



# --------------------------------------------------------------------- hunt

# Markers worth timestamping on every trial. The two known failure modes differ
# by the ORDER of the SLIM channel activation and the AFE port start, so the gap
# between those two is the first variable to correlate against success.
HUNT_EVENTS = {
    "afe": r"TKAFE port_start DEVICE_START",
    "slim": r"TKSLIM dir=1",
    "asm": r"TKASM open_read",
    "adm": r"TKADM matrix_map",
    "under": r"underflow error on TX port",
    "over": r"overflow error on TX port",
    "closed": r"Port Closed TX port",
}


def hunt_marks(dmesg):
    """{event: kernel timestamp} for the last occurrence of each marker."""
    out = {}
    for line in dmesg.splitlines():
        m = re.match(r"\[\s*([0-9.]+)\]", line)
        if not m:
            continue
        t = float(m.group(1))
        for k, pat in HUNT_EVENTS.items():
            if re.search(pat, line):
                out[k] = t
    return out


def cmd_hunt(trials, dur, sets, reboot_every=0):
    """Run one capture many times and report what differs on the ones that work.

    The mic has produced a clean 20s capture and then failed ~14 times running
    (docs/HANDOFF-audio.md section 2.0a). Against an intermittent fault a single
    run proves nothing either way, and staring at one failure is how five
    different components each got blamed in turn. So: many identical trials, a
    compact fingerprint of each, then correlate.

    A working capture is live from its very first sample, so a short recording
    classifies it just as well as a long one -- which is what makes running
    dozens of trials practical.
    """
    rows = []
    fastboot = os.environ.get(
        "FASTBOOT", os.path.expanduser("~/Android/Sdk/platform-tools/fastboot"))
    for n in range(trials):
        if reboot_every and n and n % reboot_every == 0:
            print(f"  [{n:3d}] rebooting")
            subprocess.run([os.path.join(HERE, "tk-to-fastboot.sh")],
                           capture_output=True)
            subprocess.run([fastboot, "boot", "/tmp/boot-audio-fix.img"],
                           capture_output=True)
            for _ in range(90):
                if sh("true").returncode == 0:
                    break
        tag = f"hunt-{n:03d}"
        r = capture(tag, sets, dur=dur, snap_at=max(1, dur - 2), quiet=True)
        marks = hunt_marks(open(os.path.join(LAB, tag, "dmesg.txt")).read())
        gap = None
        if "slim" in marks and "afe" in marks:
            gap = round(marks["slim"] - marks["afe"], 4)
        live = r["distinct"] > 4 and r["ac"] > 0.5
        rows.append(dict(n=n, live=live, ac=r["ac"], distinct=r["distinct"],
                         dc=r["dc"], gap=gap, marks=marks,
                         errs=len(slim_errors(tag))))
        print(f"  [{n:3d}] {'LIVE' if live else 'flat'}  ac={r['ac']:8.2f} "
              f"distinct={r['distinct']:<6} dc={r['dc']:9.1f} "
              f"slim-afe={gap}s errs={rows[-1]['errs']}")

    good = [r for r in rows if r["live"]]
    bad = [r for r in rows if not r["live"]]
    print(f"\n=== {len(good)}/{len(rows)} live ===")
    for r in good:
        print(f"  LIVE trial {r['n']}: gap={r['gap']}s errs={r['errs']} "
              f"marks={ {k: round(v,3) for k,v in r['marks'].items()} }")
    if not good or not bad:
        print("  no contrast to correlate -- run more trials")
        return rows
    for key in ("gap", "errs", "dc"):
        gv = [r[key] for r in good if r[key] is not None]
        bv = [r[key] for r in bad if r[key] is not None]
        if gv and bv:
            print(f"  {key:6}: live mean {sum(gv)/len(gv):10.4f}   "
                  f"flat mean {sum(bv)/len(bv):10.4f}")
    return rows


# -------------------------------------------------------------- slim hardware

# The SLIMbus controller registers APPS is ALLOWED to touch. Nothing else.
#
# READ THIS BEFORE ADDING AN ADDRESS. On 2026-07-29 this swept the PGD port
# block (`enum pgd_reg_v2` from downstream slim-msm.h, 0x14000 + p*0x1000) and
# HARD-HUNG the phone -- black screen, physical Power+VolDown to recover.
#
# The mistake: `pgd_reg_v2` belongs to downstream's slim-msm-**ctrl**.c, the
# path for platforms where the APPLICATION PROCESSOR is the SLIMbus master and
# owns the PGD. msm8998 uses slim-msm-**ngd**.c. NGD is *Non-ported Generic
# Device*: APPS is a satellite with NO ports of its own, and the PGD -- with
# every port register and the whole audio data path -- belongs to the ADSP.
# That range is XPU-protected from APPS, and an XPU violation on this SoC is a
# hard hang, not a fault you can catch.
#
# So the ADSP-side SLIMbus state is NOT observable from here at any address.
# It can only be reached through the ADSP itself (QMI, or AFE/ASM responses).
# A "guard" that proves the bus is clocked does not help: clocking was never
# the hazard, ownership was.
#
# What remains safe is exactly what the running kernel driver itself reads,
# because the driver reading it is proof APPS may: qcom-ngd-ctrl.c does
# `readl(ctrl->base)` for the version and `readl(ngd->base + NGD_*)`, with
# ngd->base = ctrl->base + id*0x1000 + (id-1)*0x1000, so NGD 1 = base + 0x1000.
SLIM_BASE = 0x171C0000
SLIM_REGS = {
    "HW_VERSION": 0x0000,   # qcom_slim_ngd_probe: readl_relaxed(ctrl->base)
    "NGD1_CFG": 0x1000,     # NGD_CFG    \
    "NGD1_STATUS": 0x1004,  # NGD_STATUS  | all read by qcom-ngd-ctrl.c
    "NGD1_INT_EN": 0x1010,  # NGD_INT_EN  | against ngd->base
    "NGD1_INT_STAT": 0x1014,  # NGD_INT_STAT /
}
SLIM_PORT_REGS = {}         # deliberately empty -- see the comment above

MEMLIST_PY = r"""
import mmap, os, sys
psz = mmap.PAGESIZE
fd = os.open("/dev/mem", os.O_RDONLY | os.O_SYNC)
out = []
for a in (int(x, 0) for x in sys.argv[1:]):
    base = a & ~(psz - 1)
    try:
        m = mmap.mmap(fd, psz, mmap.MAP_SHARED, mmap.PROT_READ, offset=base)
        out.append("%08x" % int.from_bytes(m[a - base:a - base + 4], "little"))
        m.close()
    except Exception:
        out.append("--------")
print(" ".join(out))
os.close(fd)
"""


def mem_words(addrs):
    """Read many 32-bit registers in ONE remote call (an ssh round trip each
    would make a full port sweep take minutes and miss the stream)."""
    args = " ".join(f"{a:#x}" for a in addrs)
    r = sh(f"sudo python3 -c '{MEMLIST_PY}' {args}")
    vals = r.stdout.split()
    if len(vals) != len(addrs):
        sys.exit("physical read failed -- needs CONFIG_DEVMEM=y, STRICT_DEVMEM off\n"
                 f"  {r.stderr.strip()}")
    return [None if v.startswith("-") else int(v, 16) for v in vals]


def cmd_slimhw(ports=0, quiet=False):
    """Dump the SLIMbus registers APPS owns. Returns {name: value}.

    `ports` is accepted and ignored: there is no APPS-visible port block on
    this SoC (see the SLIM_REGS comment). It stays in the signature so an old
    invocation fails loudly here rather than hanging the phone.
    """
    if ports:
        sys.exit("there is no APPS-visible SLIMbus port block on msm8998 -- APPS "
                 "is an NGD satellite and the PGD belongs to the ADSP. Reading "
                 "it is an XPU violation and hard-hangs the SoC (this happened). "
                 "ADSP-side state has to come from the ADSP: QMI, or AFE/ASM.")
    names = list(SLIM_REGS)
    addrs = [SLIM_BASE + SLIM_REGS[n] for n in names]
    vals = mem_words(addrs)
    out = dict(zip(names, vals))
    # Ownership was one hazard; CLOCKING is a separate one, and on this range a
    # gated read does not hang -- it returns the same constant for every
    # address. HW_VERSION is fixed in silicon, so if it agrees with everything
    # else the block was asleep and none of these values are real. Reported once
    # as a bogus "idle vs capture" delta before this check existed.
    if len(set(v for v in vals if v is not None)) == 1:
        out["_GATED"] = True
        if not quiet:
            print(f"  !! all registers read {vals[0]:#010x} -- the NGD is "
                  "clock-gated/suspended and these values are NOT real")
    if quiet:
        return out
    for n in list(SLIM_REGS):
        print(f"  {n:<22} {out[n]:08x}" if out[n] is not None else f"  {n:<22} --")
    return out


def cmd_slimdiff(dur, sets, ports=0):
    """NGD state at idle vs DURING a live capture.

    Much weaker than it was originally written to be, and honestly so: the port
    hardware that would actually answer "is the channel running" belongs to the
    ADSP and cannot be read from here at all. What is left is the NGD's own
    config/status/interrupt state, which still shows whether the satellite
    controller is enabled and whether it is fielding bus interrupts during a
    stream.
    """
    print("== idle")
    before = cmd_slimhw(ports, quiet=True)
    sh("sudo rc-service greetd stop >/dev/null 2>&1; sudo dmesg -C")
    reset_mixer()
    set_mixer(sets)
    remote = "/tmp/slimdiff.wav"
    sh(f"(arecord -D hw:0,1 -f S16_LE -r 48000 -c 1 -d {dur} {remote} "
       f">/dev/null 2>&1 &); sleep 3")
    print(f"== during capture (t+3s of {dur}s)")
    during = cmd_slimhw(ports, quiet=True)
    sh("while pgrep -x arecord >/dev/null; do sleep 1; done")

    if before.pop("_GATED", False):
        print("\n!! the idle snapshot was taken while the NGD was suspended, so "
              "there is nothing to diff against. Only the capture column below "
              "is real.")
        for k, v in during.items():
            if k != "_GATED":
                print(f"  {k:<22} {'--' if v is None else f'{v:08x}'}")
        return before, during
    during.pop("_GATED", None)

    print("\n== registers that MOVED between idle and capture")
    moved = 0
    for k in before:
        if before[k] != during[k]:
            b = "--" if before[k] is None else f"{before[k]:08x}"
            d = "--" if during[k] is None else f"{during[k]:08x}"
            print(f"  {k:<22} {b} -> {d}")
            moved += 1
    if not moved:
        print("  NOTHING -- but that is EXPECTED here, not a finding. The APPS "
              "NGD only carries control messages; the data path and its port "
              "registers live on the ADSP and are invisible from this side.")
    return before, during


# ------------------------------------------------------------------ /dev/mem

MEM_PY = r"""
import mmap, os, sys
addr, ln, wr = int(sys.argv[1], 0), int(sys.argv[2], 0), sys.argv[3]
psz = mmap.PAGESIZE
base = addr & ~(psz - 1)
off = addr - base
span = ((off + ln + psz - 1) // psz) * psz
fd = os.open("/dev/mem", os.O_RDWR if wr != "-" else os.O_RDONLY | os.O_SYNC)
m = mmap.mmap(fd, span, mmap.MAP_SHARED,
              mmap.PROT_READ | (mmap.PROT_WRITE if wr != "-" else 0), offset=base)
if wr != "-":
    m[off:off + 4] = int(wr, 0).to_bytes(4, "little")
sys.stdout.write(m[off:off + ln].hex())
m.close(); os.close(fd)
"""


def cmd_mem(addr, length, write=None, words=False):
    """Read (or write one 32-bit word of) PHYSICAL memory via /dev/mem.

    Needs CONFIG_DEVMEM=y and STRICT_DEVMEM off -- with STRICT_DEVMEM on, only
    MMIO is reachable and system RAM is refused. This is the cheapest way to
    settle "is the DSP writing where we think it is", and it also covers the
    plain register pokes this SoC needs (e.g. the LPAIF quaternary muxsel at
    0x1711d000 that downstream writes on every MI2S start and mainline never
    touches).
    """
    a = int(str(addr), 0)
    r = sh(f"sudo python3 -c '{MEM_PY}' {a:#x} {int(length)} {write or '-'}")
    hexs = r.stdout.strip()
    if not hexs:
        sys.exit("read failed -- needs CONFIG_DEVMEM=y with STRICT_DEVMEM off\n"
                 f"  {r.stderr.strip()}")
    raw = bytes.fromhex(hexs)
    if words:
        for i in range(0, len(raw) - 3, 4):
            print(f"  {a+i:#010x}: {int.from_bytes(raw[i:i+4],'little'):08x}")
    else:
        for i in range(0, len(raw), 16):
            chunk = raw[i:i + 16]
            print(f"  {a+i:#010x}: {chunk.hex(' ')}")
    return raw


# ------------------------------------------------------------------ analysis

def _fft(x):
    """Iterative radix-2 FFT.  len(x) must be a power of two."""
    n = len(x)
    if n == 1:
        return list(x)
    ev, od = _fft(x[0::2]), _fft(x[1::2])
    t = [exp(-2j * pi * k / n) * od[k] for k in range(n // 2)]
    return [ev[k] + t[k] for k in range(n // 2)] + [ev[k] - t[k] for k in range(n // 2)]


def spectrum(xs, rate, top=4):
    """Dominant bins of a Hann-windowed 4096-point frame, DC removed."""
    n = 4096
    if len(xs) < n:
        return []
    mid = (len(xs) - n) // 2
    f = xs[mid:mid + n]
    dc = sum(f) / n
    w = [(f[i] - dc) * (0.5 - 0.5 * math.cos(2 * pi * i / n)) for i in range(n)]
    mag = [abs(c) for c in _fft(w)[:n // 2]]
    peak = max(mag[1:]) or 1.0
    idx = sorted(range(1, n // 2), key=lambda i: -mag[i])[:top]
    return [(round(i * rate / n), round(mag[i] / peak, 3)) for i in sorted(idx)]


def stats(xs):
    dc = sum(xs) / len(xs)
    ac = (sum((x - dc) ** 2 for x in xs) / len(xs)) ** 0.5
    return dc, ac, len(set(xs))


def runs(s, limit=12):
    """Run-length encode the capture.  A whole 20s recording that collapses to
    two runs is not a microphone problem -- it is a stream that latched."""
    out, cur, start = [], s[0], 0
    for i in range(1, len(s)):
        if s[i] != cur:
            out.append((start, i - start, cur))
            cur, start = s[i], i
    out.append((start, len(s) - start, cur))
    return out if len(out) <= limit else out[:limit // 2] + [None] + out[-limit // 2:]


def freeze_point(s):
    """Index of the last sample change, i.e. where the stream stops advancing.

    This is the measurement the old analysis threw away.  Discarding the first
    2.5s as a "settling transient" hides the difference between two completely
    different faults: a decimator whose *input* is static (output decays, then
    holds) and a transport that carries real data for a moment and then
    *latches* (output holds a mid-signal value).  The freeze index separates
    them -- and a value that lands near a buffer or period boundary points
    straight at the DMA/port handoff rather than at the microphone.
    """
    for i in range(len(s) - 1, 0, -1):
        if s[i] != s[i - 1]:
            return i
    return 0


def verdict(path, skip=2.5, quiet=False):
    with wave.open(path) as w:
        rate, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        assert w.getsampwidth() == 2, "expected S16_LE"
        raw = w.readframes(n)
    full = list(memoryview(raw).cast("h"))
    s = full[int(skip * rate) * ch:]
    if not s:
        return dict(ok=False, note="empty after skip", secs=0, live=0, ac=0, distinct=0, dc=0)

    fz = freeze_point(full)
    dc, ac, distinct = stats(s)
    live = secs = 0
    if not quiet:
        # From t=0, so the "transient" is visible rather than assumed.
        for i in range(0, len(full) - rate, rate):
            _, sac, sd = stats(full[i:i + rate])
            print(f"    t={i//rate:<3}s ac={sac:9.2f} distinct={sd:<6}"
                  f" {'ok' if sac > 0.5 and sd > 4 else 'FLAT'}")
    for i in range(0, len(s) - rate, rate):
        _, sac, sd = stats(s[i:i + rate])
        secs += 1
        ok = sac > 0.5 and sd > 4
        live += ok
    # A capture can pass every rule above and still be carrying only half the
    # samples it claims to.  That is not hypothetical: the 2026-07-29 "first
    # working capture" was a 24kHz stream zero-order-held to 48kHz -- every
    # even sample equal to its predecessor -- and it passed section 4 outright,
    # so a real 2:1 transport defect went unnoticed for a whole session
    # (docs/HANDOFF-audio.md section 2.5).  chg is the honest sample rate; dup
    # and zeros say which way a starved port is failing (holding its last value
    # vs being flushed to zero by the auto-recovery).
    one = s[::ch]
    chg = sum(1 for i in range(1, len(one)) if one[i] != one[i - 1])
    dup = 100 * (len(one) - 1 - chg) / max(1, len(one) - 1)
    zeros = 100 * sum(1 for v in one if v == 0) / max(1, len(one))
    return dict(ok=(live == secs and secs >= 15), secs=secs, live=live,
                ac=ac, distinct=distinct, dc=dc, frames=len(full),
                freeze=fz, freeze_s=round(fz / rate, 4), tail=full[-1],
                runs=runs(full), rate=rate,
                chg=round(chg / max(1, len(one) / rate)),
                dup=round(dup, 1), zeros=round(zeros, 1),
                spec=spectrum(s, rate) if distinct > 4 else
                     spectrum(full[:fz], rate) if fz > 4096 else [])


# ------------------------------------------------------------------ capture

def reset_mixer():
    """Clear every capture route before applying a recipe.

    Not optional.  The AIF capture mixer switches are what build the codec
    DAI's channel list, and that list becomes the SLIM `payload` written to
    TX_PORT_MULTI_CHNL_0 as well as the channel map handed to the DSP.  A
    switch left on by a previous experiment silently widens the stream: an
    apparently one-channel capture then runs with TX_PORT_MULTI_CHNL_0 = 0xa0
    and a two-entry channel map, and the result is attributed to whatever the
    recipe *said* it was testing.  This cost one wrong conclusion already.
    """
    cmds = []
    for aif in (1, 2, 3):
        for tx in range(9):
            cmds.append(f"amixer -c0 -q cset name='AIF{aif}_CAP Mixer SLIM TX{tx}' 0")
    for tx in range(9):
        cmds.append(f"amixer -c0 -q cset name='CDC_IF TX{tx} MUX' ZERO")
    for mm in (1, 2):
        cmds.append(f"amixer -c0 -q cset name='MultiMedia{mm} Mixer SLIMBUS_0_TX' 0")
    sh("; ".join(c + " >/dev/null 2>&1" for c in cmds))


def set_mixer(pairs):
    """Apply 'Ctl=Val' pairs.  Refuses DECn/ADCn Volume unless VOL_OK is set."""
    cmds, bad = [], []
    for p in pairs:
        ctl, _, val = p.partition("=")
        ctl, val = ctl.strip(), val.strip()
        if re.fullmatch(r"(DEC|ADC)\d+ Volume", ctl) and not os.environ.get("TK_LAB_ALLOW_VOL"):
            sys.exit(f"refusing to write '{ctl}': kcontrol max 40 < register default 84, so "
                     "this is an unrecoverable gain cut. Set TK_LAB_ALLOW_VOL=1 to override.")
        cmds.append(f"amixer -c0 -q cset name='{ctl}' '{val}' >/dev/null 2>&1 || echo 'FAIL {ctl}'")
    r = sh("; ".join(cmds))
    bad = [l for l in r.stdout.splitlines() if l.startswith("FAIL")]
    for b in bad:
        print(f"  WARN {b}")
    return not bad


def capture(name, sets, dur=22, snap_at=6, quiet=False, mid=(), pokes=(), play=False):
    """One clean experiment: quiesce, set mixer once, record, snap regs mid-stream.

    `mid` is a list of 'SEC:Ctl=Val' changes applied *during* the stream.  That
    normally invalidates a capture (section 4: mixer changes produce decaying
    transients that read as signal), and it is only ever correct as a liveness
    probe: if the recorded value does not move when a mux upstream of it does,
    the stream is not advancing at all and nothing about the microphone can be
    concluded from it.
    """
    d = os.path.join(LAB, name)
    os.makedirs(d, exist_ok=True)
    # The device runs systemd now, and its pkill is busybox's -- which takes
    # ONE pattern and ignores the rest, so the old `pkill -9 pipewire
    # pulseaudio wireplumber` killed nothing and left the card grabbed.  That
    # is the "aplay: Resource busy" in section 6.
    sh("sudo systemctl stop greetd >/dev/null 2>&1; "
       "for p in pipewire pipewire-pulse wireplumber pulseaudio; do sudo pkill -9 -x $p; done; "
       "sleep 1")
    sh("sudo dmesg -C")
    reset_mixer()
    set_mixer(sets)
    for pk in pokes:
        reg, _, val = pk.partition("=")
        if not poke(reg, val):
            sys.exit(f"poke {reg}={val} failed -- needs "
                     "CONFIG_REGMAP_ALLOW_WRITE_DEBUGFS=y in the running kernel")
        print(f"    poke {reg} = {val}")
    if play:
        # A tone on the RX side, for the internal loopback experiment: without
        # a signal going IN, a loopback capture of silence proves nothing.
        sh("command -v sox >/dev/null && sox -n -r 48000 -c 1 -b 16 /tmp/tone.wav "
           "synth 60 sine 1000 vol 0.5 2>/dev/null || "
           "python3 -c \"import math,struct,wave;w=wave.open('/tmp/tone.wav','w');"
           "w.setnchannels(1);w.setsampwidth(2);w.setframerate(48000);"
           "w.writeframes(b''.join(struct.pack('<h',int(16000*math.sin(2*math.pi*1000*n/48000)))"
           "for n in range(48000*60)));w.close()\"")
        sh("(aplay -D hw:0,0 /tmp/tone.wav >/tmp/aplay.log 2>&1 &); sleep 1")

    remote = f"/tmp/lab-{name}.wav"
    sh(f"(arecord -D hw:0,1 -f S16_LE -r 48000 -c 1 -d {dur} {remote} "
       f">/tmp/lab-{name}.log 2>&1 &); sleep {snap_at}")
    regs = pull_regs()
    open(os.path.join(d, "regs-run.txt"), "w").write(regs)
    for m in mid:
        at, _, kv = m.partition(":")
        ctl, _, val = kv.partition("=")
        sh(f"sleep $(({at} - {snap_at})); amixer -c0 -q cset name='{ctl}' '{val}'")
        print(f"    t={at}s -> {ctl} = {val}")
    sh(f"while pgrep -x arecord >/dev/null; do sleep 1; done")
    for fn, cmd in STATE_CMDS.items():
        open(os.path.join(d, fn), "w").write(sh(cmd).stdout)

    local = os.path.join(d, "cap.wav")
    subprocess.run(["scp", "-q", "-o", "StrictHostKeyChecking=no",
                    "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                    f"{PHONE}:{remote}", local], check=False)
    if not os.path.exists(local) or os.path.getsize(local) < 1000:
        return dict(ok=False, note="no capture", secs=0, live=0, ac=0, distinct=0, dc=0)
    return verdict(local, quiet=quiet)


# ------------------------------------------------------------------ recipes

BASE_TX7 = [
    "MultiMedia2 Mixer SLIMBUS_0_TX=1",
    "AIF1_CAP Mixer SLIM TX7=1",
    "CDC_IF TX7 MUX=DEC7",
    "ADC MUX7=DMIC",
]

RECIPES = {
    # Does the transport ADVANCE, or does it latch one value?  'ZERO' proves
    # neither: a frozen sampler holding 0 is indistinguishable from a working
    # one carrying 0.  RX_MIX_TX7 is the only internal source that is both
    # non-constant and upstream of CDC_IF -- but mainline never implements the
    # 'RX MIX TXn MUX' selector, so CDC_RX_INP_MUX_RX_MIX_CFG3 needs a poke.
    "transport": [
        ("zero", BASE_TX7 + ["CDC_IF TX7 MUX=ZERO"]),
        ("rxmix", BASE_TX7 + ["CDC_IF TX7 MUX=RX_MIX_TX7"]),
        ("dec7_192", BASE_TX7 + ["CDC_IF TX7 MUX=DEC7_192"]),
    ],
    # All six DMICs through the vendor's real taimen slot (DEC7/TX7).
    "dmics": [(f"dmic{i}", BASE_TX7 + [f"DMIC MUX7=DMIC{i}"]) for i in range(6)],
    # Same mic, three times, mixer untouched between: a constant that CHANGES
    # run to run is latched residue, not a property of the pad.
    "repeat": [(f"rep{i}", BASE_TX7 + ["DMIC MUX7=DMIC0"]) for i in range(3)],
    # Every decimator slot, each on its own SLIM TX port, to prove the fault is
    # not specific to slot 7.
    "slots": [
        (f"tx{n}", [f"MultiMedia2 Mixer SLIMBUS_0_TX=1", f"AIF1_CAP Mixer SLIM TX{n}=1",
                    f"CDC_IF TX{n} MUX=DEC{n}", f"ADC MUX{n}=DMIC",
                    f"DMIC MUX{n}=DMIC{d}"])
        for n, d in ((7, 0), (5, 2), (6, 4))
    ],
    # The analog front end, for completeness: taimen wires no analog mic, so a
    # flat result here is EXPECTED and proves nothing on its own.
    "amic": [("amic1", BASE_TX7 + ["ADC MUX7=AMIC", "AMIC MUX7=ADC1"])],
}


def cmd_sweep(group, dur):
    if group not in RECIPES:
        sys.exit(f"unknown group '{group}'; have: {', '.join(RECIPES)}")
    rows = []
    for name, sets in RECIPES[group]:
        tag = f"{group}-{name}"
        print(f"\n=== {tag} ===")
        for s in sets:
            print(f"    set {s}")
        r = capture(tag, sets, dur=dur, quiet=True)
        rows.append((name, r))
        print(f"  -> dc={r['dc']:.1f} ac={r['ac']:.2f} distinct={r['distinct']} "
              f"live={r['live']}/{r['secs']} freeze@{r.get('freeze_s')}s "
              f"tail={r.get('tail')} {'PASS' if r['ok'] else 'flat'}"
              + (f"  peaks={r.get('spec')}" if r.get("spec") else ""))
        for e in slim_errors(tag):
            print(f"     SLIM {e[0]} {e[1]}{e[2]} value {e[3]}")
    print(f"\n{'recipe':<12} {'dc':>9} {'ac':>9} {'distinct':>8} {'live':>7} "
          f"{'chg/s':>7} {'dup%':>6} {'zero%':>6}  verdict")
    for name, r in rows:
        chg, rt = r.get('chg', 0), r.get('rate', 48000)
        # chg well under the nominal rate means a 2:1 (or worse) transport
        # defect that every other column here will happily call SIGNAL.
        flag = "  <- HALF RATE" if r['ok'] and chg and chg < 0.75 * rt else ""
        print(f"{name:<12} {r['dc']:9.1f} {r['ac']:9.2f} {r['distinct']:8d} "
              f"{r['live']:3d}/{r['secs']:<3d} {chg:7d} {r.get('dup',0):6.1f} "
              f"{r.get('zeros',0):6.1f}  {'SIGNAL' if r['ok'] else 'flat'}{flag}")


def main():
    os.makedirs(LAB, exist_ok=True)
    p = argparse.ArgumentParser(prog="tk-lab.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snap"); s.add_argument("tag"); s.add_argument("--no-regs", action="store_true")
    s = sub.add_parser("diff"); s.add_argument("a"); s.add_argument("b"); s.add_argument("-p", "--prefix")
    s = sub.add_parser("show"); s.add_argument("regs", nargs="+")
    s = sub.add_parser("poke"); s.add_argument("reg"); s.add_argument("val")
    s = sub.add_parser("cap"); s.add_argument("name"); s.add_argument("-d", "--dur", type=int, default=22)
    s.add_argument("-s", "--set", action="append", default=[])
    s.add_argument("-m", "--mid", action="append", default=[],
                   help="'SEC:Ctl=Val' -- liveness probe, changes the mixer DURING the stream")
    s.add_argument("-P", "--poke", action="append", default=[],
                   help="'REG=VAL' -- write a codec register before the stream")
    s.add_argument("--play", action="store_true",
                   help="play a 1 kHz tone on hw:0,0 during the capture (RX loopback)")
    s = sub.add_parser("sweep"); s.add_argument("group"); s.add_argument("-d", "--dur", type=int, default=22)
    s = sub.add_parser("verdict"); s.add_argument("wav")
    s = sub.add_parser("hunt", help="run N identical trials, correlate what differs")
    s.add_argument("-n", "--trials", type=int, default=30)
    s.add_argument("-d", "--dur", type=int, default=5)
    s.add_argument("-s", "--set", action="append", default=[])
    s.add_argument("--reboot-every", type=int, default=0)
    s = sub.add_parser("slimhw", help="dump the SLIMbus controller + PGD ports")
    s.add_argument("-p", "--ports", type=int, default=16)
    s = sub.add_parser("slimdiff", help="slimhw at idle vs DURING a capture")
    s.add_argument("-d", "--dur", type=int, default=12)
    s.add_argument("-s", "--set", action="append", default=[])
    s = sub.add_parser("mem", help="read/write PHYSICAL memory via /dev/mem")
    s.add_argument("addr"); s.add_argument("length", nargs="?", default=64)
    s.add_argument("-w", "--write", help="write this 32-bit value at addr first")
    s.add_argument("--words", action="store_true", help="print as 32-bit words")
    a = p.parse_args()

    if a.cmd == "snap":
        snap(a.tag, regs=not a.no_regs)
    elif a.cmd == "diff":
        cmd_diff(a.a, a.b, a.prefix)
    elif a.cmd == "show":
        cmd_show(a.regs)
    elif a.cmd == "poke":
        cmd_poke(a.reg, a.val)
    elif a.cmd == "cap":
        r = capture(a.name, a.set or BASE_TX7, dur=a.dur, mid=a.mid,
                    pokes=a.poke, play=a.play)
        print(f"-> dc={r['dc']:.1f} ac={r['ac']:.2f} distinct={r['distinct']} "
              f"live={r['live']}/{r['secs']} {'SIGNAL' if r['ok'] else 'flat'}")
        print(f"   peaks={r.get('spec')}")
        print("   runs (start_s, len, value):")
        for run in r.get("runs", []) or []:
            if run is None:
                print("     ...")
                continue
            st, ln, v = run
            print(f"     {st/r['rate']:9.5f}s  {ln:8d}  {v}")
    elif a.cmd == "sweep":
        cmd_sweep(a.group, a.dur)
    elif a.cmd == "hunt":
        cmd_hunt(a.trials, a.dur, a.set or (BASE_TX7 + ["DMIC MUX7=DMIC0"]),
                 a.reboot_every)
    elif a.cmd == "slimhw":
        cmd_slimhw(a.ports)
    elif a.cmd == "slimdiff":
        cmd_slimdiff(a.dur, a.set or (BASE_TX7 + ["DMIC MUX7=DMIC0"]))
    elif a.cmd == "mem":
        cmd_mem(a.addr, a.length, a.write, a.words)
    elif a.cmd == "verdict":
        r = verdict(a.wav)
        print(r)


if __name__ == "__main__":
    main()
