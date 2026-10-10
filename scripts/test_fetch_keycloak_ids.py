"""Self-test for fetch-keycloak-ids.py: `python3 -I scripts/test_fetch_keycloak_ids.py`."""
import csv
import http.server
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("fk", HERE / "fetch-keycloak-ids.py")
fk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fk)

S1 = "6f1c2a9e-0b7d-4c1e-9f3a-2d5e8b7c6a10"
S2 = "11111111-2222-4333-8444-555555555555"
KC = {"ram": [{"id": S1, "username": "ram"}], "twin": [{"id": S1, "username": "twin"}, {"id": S2, "username": "twin"}],
      "near": [{"id": S2, "username": "near-miss"}]}


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        if self.headers.get("Authorization") != "Bearer tok-1" or not self.path.startswith("/auth/admin/realms/nidan/users"):
            self.send_response(401)
            self.end_headers()
            return
        assert q["exact"] == ["true"]
        body = json.dumps(KC.get(q["username"][0], [])).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


def main():
    rows = [
        {"username": "ram", "app": "openmrs", "decision": "link", "keycloak_sub": ""},
        {"username": "Ram", "app": "odoo", "decision": "link", "keycloak_sub": ""},
        {"username": "sita", "app": "openmrs", "decision": "", "keycloak_sub": ""},          # not linked: untouched
        {"username": "hari", "app": "openmrs", "decision": "link", "keycloak_sub": S2},       # already filled: kept
        {"username": "twin", "app": "openmrs", "decision": "link", "keycloak_sub": ""},       # ambiguous
        {"username": "near", "app": "openmrs", "decision": "link", "keycloak_sub": ""},       # not exact
        {"username": "ghost", "app": "openmrs", "decision": "link", "keycloak_sub": ""},
    ]
    srv = http.server.HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}/auth"
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        with open(d / "link-table.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        (d / "token").write_text("tok-1\n")
        env = dict(os.environ, KC_TOKEN_FILE=str(d / "token"))
        r = subprocess.run([sys.executable, "-I", str(HERE / "fetch-keycloak-ids.py"), str(d), "--url", base],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 1 and "2 id(s) filled, 3 left blank" in r.stdout, r.stdout + r.stderr
        assert "tok-1" not in r.stdout + r.stderr
        got = {(x["username"], x["app"]): x["keycloak_sub"] for x in csv.DictReader(open(d / "link-table.csv"))}
        assert got[("ram", "openmrs")] == S1 and got[("Ram", "odoo")] == S1
        assert got[("sita", "openmrs")] == "" and got[("hari", "openmrs")] == S2
        assert got[("twin", "openmrs")] == got[("near", "openmrs")] == got[("ghost", "openmrs")] == ""
        (d / "token").write_text("wrong\n")
        r = subprocess.run([sys.executable, "-I", str(HERE / "fetch-keycloak-ids.py"), str(d), "--url", base],
                           capture_output=True, text=True, env=dict(env))
        assert r.returncode == 1 and "401" in r.stderr, r.stderr
    r = subprocess.run([sys.executable, "-I", str(HERE / "fetch-keycloak-ids.py"), str(HERE), "--url", base],
                       capture_output=True, text=True, env=dict(os.environ, KC_TOKEN_FILE=__file__))
    assert r.returncode == 2 and "git work tree" in r.stderr, r.stderr
    srv.shutdown()
    print("fetch-keycloak-ids: OK")


if __name__ == "__main__":
    main()
