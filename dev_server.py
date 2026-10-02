"""Local development server: serves the public HTML pages and routes /api/* to the same router Vercel runs.

    python dev_server.py            -> http://localhost:8000   (access code: glocomp-demo)

Only an explicit allow-list of public files is served, mirroring production: nothing under api/, data/ or scripts/
is reachable from the browser.
"""
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "api"))
from _lib import router  # noqa: E402

PUBLIC = {"/": "login.html", "/login": "login.html", "/employer": "index.html", "/dashboard": "index.html",
          "/employee": "employee-dashboard.html", "/architecture": "architecture.html"}
PUBLIC.update({"/" + f: f for f in ("login.html", "index.html", "employee-dashboard.html", "architecture.html",
                                    "assets/base.css", "assets/gc.css", "assets/gc.js")})
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
SECURITY_HEADERS = [
    ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer"),
    ("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"),
]


class Dev(BaseHTTPRequestHandler):
    def _api(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else b""
        req = router.Request(self.command, self.path, dict(self.headers.items()), body, ip=self.client_address[0], secure=False)
        status, headers, payload = router.dispatch(req)
        self.send_response(status)
        for k, v in headers + SECURITY_HEADERS:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._api()
        name = PUBLIC.get(self.path.split("?")[0])
        if not name:
            self.send_error(404)
            return
        with open(os.path.join(ROOT, name), "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", TYPES[os.path.splitext(name)[1]])
        self.send_header("Cache-Control", "no-store")
        for k, v in SECURITY_HEADERS:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._api()
        self.send_error(405)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print("Glocomp demo on http://localhost:%d  (access code: glocomp-demo)" % port)
    ThreadingHTTPServer(("127.0.0.1", port), Dev).serve_forever()
