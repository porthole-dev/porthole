#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Execute the real helper through distinct toolkit/worktree paths."""
import gzip
import os
import pathlib
import struct
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp)
        work = d/'work'; work.mkdir()
        toolkit = d/'porthole'; toolkit.symlink_to(ROOT, target_is_directory=True)
        dtb = d/'ref.dtb'; dtb.write_bytes(b'\xd0\x0d\xfe\xedreference')
        kernel = d/'kernel'; kernel.write_bytes(gzip.compress(b'correct kernel'))
        payload = kernel.read_bytes() + dtb.read_bytes()
        header = bytearray(4096); header[:8] = b'ANDROID!'
        struct.pack_into('<IIIIIIII', header, 8, len(payload), 0, 0, 0, 0, 0, 0, 4096)
        line = b'pmos_root_uuid=known-root'; header[64:64+len(line)] = line
        image = d/'boot.img'; image.write_bytes(header+payload)
        base = [sys.executable, str(toolkit/'tools/bootimg-verify.py'), str(image), '--dtb', str(dtb), '--kernel', str(kernel)]
        assert subprocess.run(base, capture_output=True).returncode == 0
        assert subprocess.run(base+['--expect-root-uuid','wrong-root'], capture_output=True).returncode != 0
        kernel.write_bytes(gzip.compress(b'stale kernel'))
        assert subprocess.run(base, capture_output=True).returncode != 0
        dtb.write_bytes(b'\xd0\x0d\xfe\xedstale')
        assert subprocess.run(base, capture_output=True).returncode != 0
        image.write_bytes(b'')
        assert subprocess.run(base, capture_output=True).returncode != 0
        env = dict(PATH=os.environ['PATH'], HOME=tmp, PORTHOLE_ROOT=str(toolkit),
                   PORTHOLE_DEVICE='google-taimen', PORTHOLE_WORKDIR=str(work))
        script = 'source "$PORTHOLE_ROOT/tools/ph-build.sh" >/dev/null 2>&1; _ph_require_image_tools'
        assert subprocess.run(['bash','-c',script],env=env,capture_output=True).returncode == 0
        script += '; _PH_REPO_ROOT="$PORTHOLE_WORKDIR"; _ph_require_image_tools'
        assert subprocess.run(['bash','-c',script],env=env,capture_output=True).returncode == 69
    print('image preflight: valid helper executed; missing helper, stale contents, wrong UUID and empty export rejected')


if __name__ == '__main__':
    main()
