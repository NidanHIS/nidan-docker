"""Harness SMS gateway for the email/SMS sign-in code tests: the "webhook" provider POSTs {to, text} here with a
Bearer token; GET /messages returns what arrived (newest last). Test harness only: keeps messages in memory."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "Bearer harness-sms-token"
MESSAGES = []


class Sink(BaseHTTPRequestHandler):
    def _send(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/send" or self.headers.get("Authorization") != TOKEN:
            return self._send(401)
        msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        MESSAGES.append({"to": msg.get("to"), "text": msg.get("text")})
        self._send(200, b"{}")

    def do_GET(self):
        if self.path != "/messages":
            return self._send(404)
        self._send(200, json.dumps(MESSAGES).encode())

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("0.0.0.0", 8080), Sink).serve_forever()
