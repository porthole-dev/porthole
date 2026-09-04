#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: BOOTED
# env: -
# exits: 0 same ABI · 1 CRC mismatch · 69 cannot compare
"""Compare the __versions (modversions) sections of two .ko files.

Run this BEFORE tk-push-module.sh. Exit 0 only when every symbol the new
module imports carries the same CRC as the reference module built against the
running kernel.

Why it exists: CONFIG_MODVERSIONS=y, so a module built from a config that
differs from the one the running kernel was built with is rejected by modprobe
("disagrees about version of symbol"). That reads as the patch failing, not as
the build being wrong, and it names no config at all. See AGENTS.md 1.1 Rule 3.

Reference module: pull the packaged one off the phone. Note the path --
`apk info -W /lib/modules/...` says "no owner package" because /lib is a
symlink apk does not resolve; use /usr/lib/modules/...

    scp phone:/usr/lib/modules/$(uname -r)/kernel/.../foo.ko /tmp/ref.ko
    tools/tk-modcrc.py /tmp/ref.ko linux/.output/.../foo.ko
"""
import struct, subprocess, sys, tempfile, os


def cannot(msg):
    """Exit 69, not 1. `1` is a RESULT about the modules -- "these two disagree"
    -- and a caller that gates an install on this must not read "there is no
    objcopy here" as that answer. brain/laws/exit-codes-are-an-api.md."""
    print(msg, file=sys.stderr)
    sys.exit(69)

# Resolve config through the shared lib: this is what supplies $PHONE, the
# mandatory ssh flags (host keys change every boot) and connection
# multiplexing. A tool that builds its own ssh command line gets none of them.
import pathlib as _pl, sys as _sys
for _p in _pl.Path(__file__).resolve().parents:
    if (_p / "lib" / "porthole.py").is_file():
        _sys.path.insert(0, str(_p / "lib"))
        break
import porthole

def versions(ko):
    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as t:
        tmp = t.name
    for tool in ('llvm-objcopy', 'objcopy'):
        try:
            if subprocess.run([tool, '-O', 'binary', '--only-section=__versions', ko, tmp],
                              capture_output=True).returncode == 0:
                break
        except FileNotFoundError:
            # an absent objcopy is "cannot compare" (69), not a CRC mismatch (1):
            # the caller refuses the push on 1, and a traceback exits 1
            continue
    else:
        cannot(f'could not extract __versions from {ko}')
    data = open(tmp, 'rb').read()
    os.unlink(tmp)
    if not data or len(data) % 64:
        cannot(f'{ko}: __versions is {len(data)} bytes, not a multiple of 64')
    return {data[i+8:i+64].split(b'\0')[0].decode(): struct.unpack('<Q', data[i:i+8])[0]
            for i in range(0, len(data), 64)}

ref, new = sys.argv[1], sys.argv[2]
a, b = versions(ref), versions(new)
bad = sorted(k for k in a.keys() & b.keys() if a[k] != b[k])
print(f'reference {ref}: {len(a)} symbols')
print(f'new       {new}: {len(b)} symbols')
print(f'only in new: {sorted(b.keys() - a.keys())}')
print(f'only in reference: {sorted(a.keys() - b.keys())}')
print(f'CRC mismatches: {len(bad)}')
for k in bad[:20]:
    print(f'  {k} {a[k]:#x} -> {b[k]:#x}')
sys.exit(1 if bad else 0)
