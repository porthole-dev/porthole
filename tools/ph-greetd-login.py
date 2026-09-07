#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device as root (the greetd socket is owned by the greeter user); greetd with a running greeter
# env: TK_LOGIN_USER (default: the config layer's PORTHOLE_USER or "user"), TK_LOGIN_PASSWORD (required, never logged)
# exits: 0 session started · 1 greetd refused · 2 bad usage
"""Log a user into the graphical session through greetd's IPC, from ssh.

Every phoc/phosh experiment ends in a session restart, and the phrog lock
screen then waits for a human thumb. greetd's protocol (u32 length-prefixed
JSON on $GREETD_SOCK) is what the greeter itself speaks: create_session ->
answer the password prompt -> start_session with the phosh command. The
password comes from the environment on purpose and is not echoed anywhere.
  TK_LOGIN_PASSWORD=... tk-greetd-login.py [USER] [SESSION-CMD...]
"""
import json, os, socket, struct, sys, time

import glob
# greetd names its socket after its own pid (/run/greetd-<pid>.sock); only the greeter gets it in $GREETD_SOCK
sock = os.environ.get("GREETD_SOCK") or (sorted(glob.glob("/run/greetd-*.sock"), key=os.path.getmtime) or ["/run/greetd.sock"])[-1]
user = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TK_LOGIN_USER", "user")
cmd = sys.argv[2:] or ["phosh-session"]
pw = os.environ.get("TK_LOGIN_PASSWORD")
if not pw:
    print("TK_LOGIN_PASSWORD not set", file=sys.stderr); sys.exit(2)

def call(s, msg):
    data = json.dumps(msg).encode()
    s.sendall(struct.pack("=I", len(data)) + data)
    n = struct.unpack("=I", s.recv(4))[0]
    return json.loads(s.recv(n))

s = socket.socket(socket.AF_UNIX); s.connect(sock)
r = call(s, {"type": "create_session", "username": user})
for _ in range(6):
    if r.get("type") == "auth_message":
        kind = r.get("auth_message_type")
        r = call(s, {"type": "post_auth_message_response", "response": pw if kind == "secret" else ""})
    elif r.get("type") == "success":
        r = call(s, {"type": "start_session", "cmd": cmd, "env": ["XDG_SESSION_TYPE=wayland", "XDG_SESSION_DESKTOP=phosh", "XDG_CURRENT_DESKTOP=Phosh:GNOME"]})
        print("start_session:", r.get("type")); sys.exit(0 if r.get("type") == "success" else 1)
    else:
        print("greetd:", r.get("type"), r.get("error_type", ""), r.get("description", ""), file=sys.stderr); sys.exit(1)
print("gave up after 6 rounds", file=sys.stderr); sys.exit(1)
