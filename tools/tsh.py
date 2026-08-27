#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: INITRAMFS (runs on the host; the device must be in the debug shell)
# env: HOST, PORTHOLE_HOST
# exits: 0 ok · non-zero on failure
"""Run a command on the pmOS initramfs debug shell (busybox telnetd, $HOST:23).

The debug shell is a raw /bin/sh behind busybox telnetd, so it opens with a short IAC
negotiation burst that has to be stripped before the output makes sense. Reads until the
link goes quiet rather than looking for a prompt -- the initramfs shell may not print one.

Usage: tsh.py "dmesg"            # prints to stdout
       tsh.py "dmesg" -o out.txt
"""
import argparse
import os
import socket
import sys
import time

IAC, DONT, WONT = 255, 254, 252


def strip_iac(buf):
    """Drop telnet IAC sequences, answering nothing (we never enable options)."""
    out = bytearray()
    i = 0
    while i < len(buf):
        if buf[i] == IAC and i + 1 < len(buf):
            cmd = buf[i + 1]
            if cmd in (251, 252, 253, 254):   # WILL/WONT/DO/DONT + option byte
                i += 3
                continue
            if cmd == 250:                     # SB ... SE
                end = buf.find(bytes([IAC, 240]), i)
                i = len(buf) if end == -1 else end + 2
                continue
            i += 2
            continue
        out.append(buf[i])
        i += 1
    return bytes(out)


def run(host, port, cmd, settle, budget):
    s = socket.create_connection((host, port), timeout=10)
    s.settimeout(1.0)

    # Refuse every option the server offers, so it stops negotiating and gives us a shell.
    time.sleep(0.5)
    try:
        first = s.recv(4096)
        replies = bytearray()
        i = 0
        while i < len(first):
            if first[i] == IAC and i + 2 < len(first):
                opt = first[i + 2]
                replies += bytes([IAC, DONT if first[i + 1] == 251 else WONT, opt])
                i += 3
            else:
                i += 1
        if replies:
            s.sendall(bytes(replies))
    except socket.timeout:
        pass

    s.sendall(cmd.encode() + b"\n")

    chunks = bytearray()
    last = time.time()
    start = time.time()
    while time.time() - start < budget:
        try:
            d = s.recv(65536)
            if not d:
                break
            chunks += d
            last = time.time()
        except socket.timeout:
            if time.time() - last > settle:
                break
    s.close()
    return strip_iac(bytes(chunks)).decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command")
    ap.add_argument("--host", default=os.environ.get("PORTHOLE_HOST", "$HOST"))
    ap.add_argument("--port", type=int, default=23)
    ap.add_argument("-o", "--output")
    ap.add_argument("--settle", type=float, default=2.0,
                    help="seconds of silence that means the command finished")
    ap.add_argument("--budget", type=float, default=45.0, help="hard cap on total read time")
    a = ap.parse_args()

    try:
        text = run(a.host, a.port, a.command, a.settle, a.budget)
    except OSError as e:
        sys.exit("connect/read failed: %s" % e)

    if a.output:
        open(a.output, "w").write(text)
        print("wrote %s (%d bytes, %d lines)" % (a.output, len(text), text.count("\n")))
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
