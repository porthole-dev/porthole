#!/usr/bin/env python3
# scope: generic
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

def versions(ko):
    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as t:
        tmp = t.name
    for tool in ('llvm-objcopy', 'objcopy'):
        if subprocess.run([tool, '-O', 'binary', '--only-section=__versions', ko, tmp],
                          capture_output=True).returncode == 0:
            break
    else:
        sys.exit(f'could not extract __versions from {ko}')
    data = open(tmp, 'rb').read()
    os.unlink(tmp)
    if not data or len(data) % 64:
        sys.exit(f'{ko}: __versions is {len(data)} bytes, not a multiple of 64')
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
