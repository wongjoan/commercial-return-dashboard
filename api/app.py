"""Vercel Python serverless entrypoint. vercel.json rewrites /api/<route> -> /api/app?route=<route>."""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _lib import router  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def _handle(self):
        length = int(self.headers.get("content-length") or 0)
        if length > 64 * 1024:
            self.send_response(413)
            self.end_headers()
            return
        body = self.rfile.read(length) if length else b""
        ip = (self.headers.get("x-forwarded-for") or self.client_address[0] or "?").split(",")[0].strip()
        secure = self.headers.get("x-forwarded-proto", "https") == "https"
        req = router.Request(self.command, self.path, dict(self.headers.items()), body, ip=ip, secure=secure)
        status, headers, payload = router.dispatch(req)
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _handle
    do_POST = _handle

    def log_message(self, fmt, *args):  # keep request bodies/cookies out of logs
        sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))
