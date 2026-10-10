"""Every upstream of the gateway in one process, for the gateway contract harness (backlog KC-42-01).

App ports echo {"path", "headers"} as JSON for any method and set an app-style session cookie, so a test
sees exactly what the gateway forwarded. Port 4180 is a stand-in oauth2-proxy: cookie
_oauth2_proxy=ok:<role,role> -> /oauth2/auth 202 when the requested allowed_groups role is listed, 403 when
not; no cookie -> 401. /oauth2/start -> 302 to the IdP; /oauth2/userinfo -> the roles as gateway groups.
"""
import http.server
import json
import re
import threading
import urllib.parse

APP_PORTS = (80, 5000, 8042, 8069, 8080, 8081, 8088)
OAUTH2_PORT = 4180


class App(http.server.BaseHTTPRequestHandler):
    def _echo(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if re.search(r"/(auth/)?[Ll]ogout/?$", self.path.split("?")[0]):
            # an app's own logout answers with a redirect, which the gateway's logout chain turns into
            # /oauth2/sign_out (proxy/auth/logout-chain.conf, KC-19)
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.dumps({"path": self.path, "method": self.command,
                           "headers": {k.lower(): v for k, v in self.headers.items()}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Server", "Stub/9.9.9")  # a version the gateway must not pass on
        self.send_header("Set-Cookie", "JSESSIONID=abc; Path=/openmrs; HttpOnly")
        self.send_header("Set-Cookie", "other=1; Path=/")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _echo

    def log_message(self, *args):
        pass


class OAuth2(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        cookies = dict(c.strip().split("=", 1) for c in (self.headers.get("Cookie") or "").split(";") if "=" in c)
        token = cookies.get("_oauth2_proxy", "")
        roles = token[3:].split(",") if token.startswith("ok:") else None
        if url.path == "/oauth2/auth":
            want = query.get("allowed_groups", [""])[0].split(":")[-1]
            code = 401 if roles is None else (202 if want in roles else 403)
            self.send_response(code)
            if code == 202:
                self.send_header("Set-Cookie", "_oauth2_proxy=refreshed; Path=/; Secure; HttpOnly")
                self.send_header("X-Auth-Request-User", "nurse1")
            self.end_headers()
            return
        if url.path == "/oauth2/start":
            self.send_response(302)
            self.send_header("Location", "https://kc.example/auth/realms/nidan/protocol/openid-connect/auth?client_id=nidan-gateway"
                             "&response_type=code&state=" + urllib.parse.quote(query.get("rd", [""])[0], safe=""))
            self.end_headers()
            return
        if url.path == "/oauth2/userinfo":
            if roles is None:
                self.send_response(401)
                self.end_headers()
                return
            body = json.dumps({"user": "nurse1", "groups": ["role:nidan-gateway:" + r for r in roles]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(url.path.encode())


def serve(port, handler):
    http.server.ThreadingHTTPServer(("0.0.0.0", port), handler).serve_forever()


if __name__ == "__main__":
    for p in APP_PORTS:
        threading.Thread(target=serve, args=(p, App), daemon=True).start()
    serve(OAUTH2_PORT, OAuth2)
