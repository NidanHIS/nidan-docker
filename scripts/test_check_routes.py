"""Self-test for check-routes.py parsing: `python3 -I scripts/test_check_routes.py`."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("cr", Path(__file__).resolve().parent / "check-routes.py")
cr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cr)

CONF = """
http {
  server {
    listen 443 ssl;
    auth_request /_auth_app-x;    # server-level gate, inherited
    location = /_auth_app-x { internal; proxy_pass http://op:4180/oauth2/auth; }
    location = /ping { auth_request /_ping; proxy_pass http://p; }  # a probe subrequest is not a login gate
    location /app/ { proxy_pass http://app; }                   # inherits the gate
    location = /health { auth_request off; proxy_pass http://h; }  # explicit exemption
    location ~ ^/app/(admin|x) { proxy_pass http://app; }        # regex still inherits
    location /deny/ { return 404; }
    # location /commented/ { proxy_pass http://nope; }
  }
  server {
    listen 80;
    location / { return 301 https://$host$request_uri; }
  }
}
"""
locs = {(l["match"], l["path"]): l for l in cr.parse_nginx(CONF)}
gated = lambda k: locs[k]["auth"] if locs[k]["auth"] is not None else locs[k]["server"]["auth"]
assert ("prefix", "/commented/") not in locs
assert gated(("prefix", "/app/")) is True
assert gated(("regex", "^/app/(admin|x)")) is True
assert gated(("exact", "/health")) is False
assert gated(("exact", "/ping")) is False
assert locs[("prefix", "/deny/")]["proxies"] is False
assert locs[("prefix", "/")]["server"]["ports"] == ["80"]
assert locs[("prefix", "/app/")]["server"]["ports"] == ["443"]

# includes of /etc/nginx/auth/*.conf are inlined from proxy/auth (SSO-on form); role and return recorded
import tempfile
with tempfile.TemporaryDirectory() as d:
    (Path(d) / "gate-app-x.conf").write_text("auth_request /_auth_app-x;\n")
    (Path(d) / "outer.conf").write_text("include /etc/nginx/auth/gate-app-x.conf;\n")
    conf2 = """http { server { listen 443 ssl;
      location /a/ { include /etc/nginx/auth/outer.conf; proxy_pass http://u; }
      location = /b { include /etc/nginx/auth/gate-app-x.conf; return 200 "leak"; }
      location = /_auth_app-x { internal; proxy_pass http://o; }
    } }"""
    got = {l["path"]: l for l in cr.parse_nginx(cr.inline_auth(conf2, Path(d)))}
assert got["/a/"]["auth"] is True and got["/a/"]["role"] == "app-x", got["/a/"]
assert got["/b"]["returns"] == ["200"]
assert got["/_auth_app-x"].get("internal") is True
# the real nginx.conf: nothing the contract gates is ungated, wrongly gated or bypassed by return
import subprocess, sys
out = subprocess.run([sys.executable, "-I", str(Path(cr.__file__)), ], capture_output=True, text=True).stdout
assert not any(w in out for w in ("UNGATED", "WRONGGATE", "RETURN ")), out
print("check-routes self-test: OK")
