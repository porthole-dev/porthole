#!/usr/bin/env python3
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Read/patch the kernel cmdline of an Android boot image, in place or to a copy.

Why this exists: the boot header's id[] SHA1 covers only (kernel, ramdisk, second) --
NOT the cmdline field. So cmdline-only edits produce an image the bootloader still
accepts, and any result you get from one is valid. Kernel/DTB replacements are the
risky ones. See the taimen-blind-debug-failure notes.

Usage:
  bootimg-cmdline.py show    IMG
  bootimg-cmdline.py patch   IMG -o OUT [--add TOK ...] [--remove PREFIX ...] [--set "FULL"]

Examples:
  # what is in there now
  ./bootimg-cmdline.py show /tmp/postmarketOS-export/boot.img

  # liveness probe: does the kernel reach kernel_init? (panics + warm-reboots if it does)
  ./bootimg-cmdline.py patch /tmp/postmarketOS-export/boot.img -o /tmp/probe.img \
      --add rdinit=/nonexistent --add init=/nonexistent

  # register ramoops at postcore_initcall instead of waiting for the DT path
  ./bootimg-cmdline.py patch /tmp/postmarketOS-export/boot.img -o /tmp/early-pstore.img \
      --add ramoops.mem_address=0xb0000000 --add ramoops.mem_size=0x200000 \
      --add ramoops.record_size=0x20000 --add ramoops.console_size=0x100000 \
      --add ramoops.pmsg_size=0x80000 --add initcall_debug
"""
import argparse
import struct
import sys

MAGIC = b'ANDROID!'
CMDLINE_OFF, CMDLINE_LEN = 64, 512
EXTRA_OFF, EXTRA_LEN = 608, 1024


def read_header(data):
    if data[:8] != MAGIC:
        sys.exit("not an Android boot image (no ANDROID! magic)")
    hdr_v = struct.unpack_from('<I', data, 40)[0]
    cmdline = data[CMDLINE_OFF:CMDLINE_OFF + CMDLINE_LEN].split(b'\0')[0]
    extra = data[EXTRA_OFF:EXTRA_OFF + EXTRA_LEN].split(b'\0')[0]
    return hdr_v, (cmdline + extra).decode('ascii', 'replace')


def write_cmdline(data, full):
    raw = full.encode('ascii')
    # The bootloader concatenates cmdline + extra_cmdline, so a long cmdline
    # spills into the extra field exactly the way mkbootimg splits it.
    if len(raw) > (CMDLINE_LEN - 1) + (EXTRA_LEN - 1):
        sys.exit("cmdline is %d bytes, max is %d"
                 % (len(raw), (CMDLINE_LEN - 1) + (EXTRA_LEN - 1)))
    head, tail = raw[:CMDLINE_LEN - 1], raw[CMDLINE_LEN - 1:]
    data[CMDLINE_OFF:CMDLINE_OFF + CMDLINE_LEN] = head.ljust(CMDLINE_LEN, b'\0')
    data[EXTRA_OFF:EXTRA_OFF + EXTRA_LEN] = tail.ljust(EXTRA_LEN, b'\0')
    return len(head), len(tail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=('show', 'patch'))
    ap.add_argument('image')
    ap.add_argument('-o', '--output', help="write here (required for patch)")
    ap.add_argument('--add', action='append', default=[], metavar='TOK',
                    help="append a token; replaces any existing token with the same key")
    ap.add_argument('--remove', action='append', default=[], metavar='PREFIX',
                    help="drop every token starting with PREFIX (e.g. 'panic=' or 'quiet')")
    ap.add_argument('--set', metavar='FULL', help="replace the whole cmdline")
    args = ap.parse_args()

    data = bytearray(open(args.image, 'rb').read())
    hdr_v, current = read_header(data)

    if args.action == 'show':
        print("header_version = %d" % hdr_v)
        print("cmdline (%d bytes):" % len(current))
        for tok in current.split():
            print("  %s" % tok)
        return

    if not args.output:
        sys.exit("patch needs -o OUT")

    toks = current.split() if args.set is None else args.set.split()

    for prefix in args.remove:
        toks = [t for t in toks if not t.startswith(prefix)]

    for tok in args.add:
        key = tok.split('=', 1)[0] + '=' if '=' in tok else tok
        toks = [t for t in toks if not (t == tok or (('=' in tok) and t.startswith(key)))]
        toks.append(tok)

    new = ' '.join(toks)
    head, tail = write_cmdline(data, new)
    open(args.output, 'wb').write(bytes(data))

    print("old: %s" % current)
    print("new: %s" % new)
    print("wrote %s (%d bytes in cmdline[], %d in extra_cmdline[])"
          % (args.output, head, tail))
    # Re-read the file we just wrote rather than trusting the buffer.
    _, back = read_header(bytearray(open(args.output, 'rb').read()))
    if back != new:
        sys.exit("VERIFY FAILED: read back %r" % back)
    print("verified: cmdline reads back identical")


if __name__ == '__main__':
    main()
