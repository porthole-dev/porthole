#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# scope: generic
# needs: on-device (python3)
# env: -
# exits: 0 ok
"""Serve a directory over HTTP with byte-range support, for <video> benches.

WebKit's media loader issues Range requests and sits on "Loading" forever
against python's stock http.server, which ignores them (2026-09-02). This is
that server plus the one header it lacks. Stdlib only.
  ph-rangehttp.py [PORT] [DIR]      default 8080, cwd, binds 127.0.0.1
"""
import http.server, os, re, sys
from http import HTTPStatus

class H(http.server.SimpleHTTPRequestHandler):
    def send_head(self):
        path = self.translate_path(self.path)
        rng = self.headers.get("Range")
        if not rng or not os.path.isfile(path):
            return super().send_head()
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        size = os.path.getsize(path)
        start = int(m.group(1)) if m.group(1) else max(0, size - int(m.group(2)))
        end = int(m.group(2)) if m.group(1) and m.group(2) else size - 1
        end = min(end, size - 1)
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        f = open(path, "rb"); f.seek(start)
        self.wfile.write(f.read(end - start + 1)); f.close()
        return None
    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes") if "Accept-Ranges" not in str(self._headers_buffer) else None
        super().end_headers()
    def log_message(self, *a): pass

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
if len(sys.argv) > 2: os.chdir(sys.argv[2])
http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
