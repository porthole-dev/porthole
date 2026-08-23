#!/usr/bin/env python3
# scope: generic
# needs: - (host only, no device)
# env: -
# exits: 0 ok · non-zero on failure
"""Generate sustained traffic that REALLY goes out over LTE. Run ON THE DEVICE.

Exists because of one trap: binding a socket to the modem's source ADDRESS is
not enough. With wlan0 up, the route still egresses via WiFi, so an "LTE" arm of
the hang matrix silently becomes a second WiFi arm and the matrix proves
nothing. SO_BINDTODEVICE is the only thing that pins egress to the interface.

  tk-lte-load.py [SECONDS] [IFACE]     default 900 s on qmapmux0.0
"""
import socket, sys, time

SECS = int(sys.argv[1]) if len(sys.argv) > 1 else 900
IFACE = (sys.argv[2] if len(sys.argv) > 2 else "qmapmux0.0").encode()
SO_BINDTODEVICE = 25

# A plain HTTP GET loop is enough load and needs no server we control.
HOSTS = [("ping.online.net", 80), ("speedtest.tele2.net", 80)]

end = time.time() + SECS
n = err = 0
while time.time() < end:
    for host, port in HOSTS:
        if time.time() >= end:
            break
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # The whole point of this file.
            s.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, IFACE)
            s.settimeout(15)
            s.connect((host, port))
            s.sendall(b"GET /10M.iso HTTP/1.0\r\nHost: %s\r\n\r\n" % host.encode())
            got = 0
            while got < 4 << 20:
                b = s.recv(65536)
                if not b:
                    break
                got += len(b)
            s.close()
            n += 1
        except Exception as e:
            err += 1
            if err % 20 == 1:
                print(f"error on {IFACE.decode()}: {e}", flush=True)
            time.sleep(2)
print(f"done: {n} transfers, {err} errors over {IFACE.decode()}", flush=True)
