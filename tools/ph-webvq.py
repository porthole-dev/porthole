#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device; Epiphany launched with WEBKIT_INSPECTOR_HTTP_SERVER=127.0.0.1:9222
# env: TK_INSPECTOR (host:port, default 127.0.0.1:9222)
# exits: 0 ok · 1 no target / no video
"""Poll a <video>'s getVideoPlaybackQuality() through WebKit's remote inspector.

The DPU vsync counter and the V4L2 queue trace both went clean while the user
still saw frames repeat: only the presenter knows which frame it showed.
`droppedVideoFrames` is WebKit's own count of frames it decided not to
present -- the one number that tracks "video not smooth". Stdlib only: a
40-line RFC 6455 client, because neither end has a websocket library.

  ph-webvq.py [SECONDS] [INTERVAL]      default 20 s at 1 Hz
Columns: t, total frames, dropped (delta per interval), videoWidth x Height,
currentTime, readyState, paused.
"""
import base64, json, os, re, socket, struct, sys, time, urllib.request

HOST = os.environ.get("TK_INSPECTOR", "127.0.0.1:9222")

def ws_connect(host, path):
    h, p = host.split(":"); s = socket.create_connection((h, int(p)), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    resp = b""
    while b"\r\n\r\n" not in resp: resp += s.recv(4096)
    if b" 101 " not in resp.split(b"\r\n")[0]: raise SystemExit("websocket handshake refused: " + resp.split(b"\r\n")[0].decode())
    return s

def ws_send(s, text):
    data = text.encode(); mask = os.urandom(4)
    hdr = bytes([0x81])
    n = len(data)
    if n < 126: hdr += bytes([0x80 | n])
    elif n < 65536: hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
    else: hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
    s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

def _recvn(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk: raise SystemExit("websocket closed")
        buf += chunk
    return buf

def ws_recv(s):
    while True:
        b0, b1 = _recvn(s, 2); op = b0 & 0x0F; n = b1 & 0x7F
        if n == 126: n = struct.unpack(">H", _recvn(s, 2))[0]
        elif n == 127: n = struct.unpack(">Q", _recvn(s, 8))[0]
        if b1 & 0x80: mask = _recvn(s, 4); data = bytes(b ^ mask[i % 4] for i, b in enumerate(_recvn(s, n)))
        else: data = _recvn(s, n)
        if op == 1: return data.decode()
        if op == 8: raise SystemExit("websocket closed by peer")
        if op == 9: s.sendall(bytes([0x8A, 0x80]) + os.urandom(4))  # pong, masked, empty

def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 20
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 1
    html = urllib.request.urlopen(f"http://{HOST}/", timeout=5).read().decode()
    targets = re.findall(r"/socket/(\d+)/(\d+)/(\w+)", html)
    if not targets: raise SystemExit("no inspector target (is Epiphany running with WEBKIT_INSPECTOR_HTTP_SERVER?)")
    cid, tid, typ = targets[-1]
    s = ws_connect(HOST, f"/socket/{cid}/{tid}/{typ}")
    expr = ("(function(){var v=document.querySelector('video');if(!v)return 'novideo';"
            "var q=v.getVideoPlaybackQuality();return JSON.stringify({t:q.totalVideoFrames,d:q.droppedVideoFrames,"
            "w:v.videoWidth,h:v.videoHeight,ct:Math.round(v.currentTime*10)/10,rs:v.readyState,p:v.paused})})()")
    msgid = 0; last = None; t0 = time.monotonic()
    print("target %s/%s %s" % (cid, tid, typ))
    # A WebPage target multiplexes: the real page is a sub-target announced by
    # Target.targetCreated, and every domain message travels inside
    # Target.sendMessageToTarget / comes back in Target.dispatchMessageFromTarget.
    page = None
    ws_send(s, json.dumps({"id": 0, "method": "Target.setPauseOnStart", "params": {"pauseOnStart": False}}))
    deadline = time.monotonic() + 5
    while page is None and time.monotonic() < deadline:
        r = json.loads(ws_recv(s))
        if r.get("method") == "Target.targetCreated": page = r["params"]["targetInfo"]["targetId"]
    if page is None: raise SystemExit("no Target.targetCreated within 5 s")
    def evaluate(expr):
        nonlocal msgid
        msgid += 1
        inner = json.dumps({"id": msgid, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True, "includeCommandLineAPI": True}})
        ws_send(s, json.dumps({"id": 1000 + msgid, "method": "Target.sendMessageToTarget", "params": {"targetId": page, "message": inner}}))
        while True:
            r = json.loads(ws_recv(s))
            if r.get("method") == "Target.dispatchMessageFromTarget":
                m = json.loads(r["params"]["message"])
                if m.get("id") == msgid: return m
    while time.monotonic() - t0 < seconds:
        r = evaluate(expr)
        if "error" in r: print("error:", r["error"]); return 1
        val = r["result"]["result"].get("value")
        if val == "novideo": print("t=%4.0f no <video>" % (time.monotonic() - t0)); time.sleep(interval); continue
        q = json.loads(val)
        dd = (q["d"] - last["d"]) if last else 0; dt = (q["t"] - last["t"]) if last else 0
        print("t=%4.0f frames=%6d (+%3d) dropped=%5d (+%2d) %dx%d ct=%s rs=%d %s" % (
            time.monotonic() - t0, q["t"], dt, q["d"], dd, q["w"], q["h"], q["ct"], q["rs"], "PAUSED" if q["p"] else "playing"))
        last = q; time.sleep(interval)
    return 0

if __name__ == "__main__":
    sys.exit(main())
