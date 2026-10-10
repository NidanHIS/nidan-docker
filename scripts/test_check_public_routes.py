"""Self-test for check-public-routes.py: run with `python3 -I scripts/test_check_public_routes.py`."""
import importlib.util
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("cpr", HERE / "check-public-routes.py")
cpr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpr)

ODOO = '''
from odoo import http
class C(http.Controller):
    @http.route("/api/listed", type="http", auth="public", methods=["POST"], csrf=False)
    def a(self): pass
    @http.route(["/api/new", "/api/new2"],
                type="http",
                auth="none")
    def b(self): pass
    @http.route("/web/private", auth="user")
    def c(self): pass
'''
SPRING = '''
public static final String[] OPEN = { "/open/**", "/also" };
http.authorizeHttpRequests(auth -> auth
    .requestMatchers("/hook").permitAll()
    .requestMatchers(OPEN).permitAll()
    .anyRequest().authenticated());
'''


def run(allow_text):
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "odoo_19_addons/m/controllers").mkdir(parents=True)
        (root / "odoo_19_addons/m/controllers/main.py").write_text(ODOO)
        cfg = root / cpr.SPRING_FILES["cis"]
        cfg.parent.mkdir(parents=True)
        cfg.write_text(SPRING)
        allow = root / "public-routes.allow"
        allow.write_text(allow_text)
        cpr.ALLOW_FILE = allow
        found = sorted((c, p) for c, p, _ in list(cpr.odoo_public_routes(root)) + list(cpr.spring_public_routes(root)))
        import io, contextlib, sys
        buf = io.StringIO()
        sys.argv = ["x", str(root)]
        with contextlib.redirect_stdout(buf):
            code = cpr.main()
        return found, code, buf.getvalue()


found, code, out = run("odoo | /api/listed | shared-secret | x\nodoo | /gone | static-asset | x\n")
assert found == [("cis", "/also"), ("cis", "/hook"), ("cis", "/open/**"),
                 ("odoo", "/api/listed"), ("odoo", "/api/new"), ("odoo", "/api/new2")], found
assert code == 1
assert "UNLISTED  odoo /api/new" in out and "UNLISTED  cis /hook" in out and "STALE     odoo /gone" in out
assert "/web/private" not in out

full = "".join(f"{c} | {p} | shared-secret | x\n" for c, p in found)
assert run(full)[1] == 0
assert run(full.replace("/hook | shared-secret", "/hook | NONE"))[1] == 1
print("check-public-routes self-test: OK")
