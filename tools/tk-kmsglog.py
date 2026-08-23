#!/usr/bin/env python3
# scope: generic
"""Follow /dev/kmsg into a file, fsync'd per record, so the kernel log tail
survives a watchdog reset.

Why this exists: /sys/fs/pstore is empty after a reset on this device (see
docs/BLUEPRINT/BP-07-pstore-ramoops.md) and the journal loses its tail --
`journalctl -b -1` ends at "Filesystems sync" and never records the hang.
This is the dumbest possible durable channel: one record, one write, one fsync.

# ponytail: CEILING -- userspace is FROZEN during suspend, so this cannot
# witness a hang in the pre-thaw resume window (device-resume / noirq). It
# guarantees the pre-freeze tail and anything after thaw, and nothing more.
# Upgrade path is pstore/ramoops or the EDL ramdump, both in BP-07.
"""
import errno, os, sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "/var/log/taimen-kmsg.log"
CAP = 16 << 20  # rotate at 16 MiB; .1 keeps the previous boot / previous window


def _rotate(path):
    try:
        os.replace(path, path + ".1")
    except FileNotFoundError:
        pass
    fd = os.open(os.path.dirname(path) or "/", os.O_RDONLY)
    os.fsync(fd)  # make the rename itself durable
    os.close(fd)


def follow(path=PATH, cap=CAP):
    _rotate(path)
    out = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    kmsg = os.open("/dev/kmsg", os.O_RDONLY)  # blocking: read() waits for a record
    written = 0
    while True:
        try:
            rec = os.read(kmsg, 8192)
        except OSError as e:
            if e.errno in (errno.EPIPE, errno.EINTR):
                continue  # EPIPE: ring wrapped past us, kernel reset our position
            raise
        if not rec:
            continue
        written += os.write(out, rec)
        os.fsync(out)
        if written >= cap:
            os.close(out)
            _rotate(path)
            out = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
            written = 0


def _selftest():
    import tempfile
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.log")
    open(p, "w").write("old")
    _rotate(p)
    assert open(p + ".1").read() == "old", "rotation must preserve the previous file"
    assert not os.path.exists(p), "rotation must leave the live path free"
    fd = os.open(p, os.O_WRONLY | os.O_CREAT, 0o640)
    for i in range(100):
        os.write(fd, b"6,%d,0,-;line %d\n" % (i, i))
        os.fsync(fd)
    os.close(fd)
    assert open(p).read().count("\n") == 100, "every fsync'd record must be readable"
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        follow()
