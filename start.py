import os, runpy, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import miniapp
except Exception:
    miniapp = None


class H(BaseHTTPRequestHandler):
    def _send(self, status, ctype, body):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _mini(self, method, body=b""):
        if miniapp is None:
            return False
        try:
            res = miniapp.handle(method, self.path, self.headers, body)
        except Exception:
            return False
        if res is None:
            return False
        self._send(*res)
        return True

    def do_GET(self):
        if self._mini("GET"):
            return
        self._send(200, "text/plain", b"ok")

    def do_POST(self):
        try:
            n = min(int(self.headers.get("Content-Length", "0")), 65536)
        except ValueError:
            n = 0
        body = self.rfile.read(n) if n else b""
        if self._mini("POST", body):
            return
        self._send(404, "text/plain", b"not found")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def serve():
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), H).serve_forever()


threading.Thread(target=serve, daemon=True).start()
runpy.run_path("bot.py", run_name="__main__")
