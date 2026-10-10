"""Self-test for disable-sso.py (fake docker runner): `python3 -I scripts/test_disable_sso.py`."""
import importlib.util
import json
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("ds", Path(__file__).resolve().parent / "disable-sso.py")
ds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ds)

words = ds.demo_words()
assert words[:2] == ["Admin123", "admin"] and "password123" in words and len(words) == len(set(words))


def fake(answers):
    """answers: container -> (rc, out) or callable(script, stdin)"""
    calls = []
    def run(container, script, stdin=""):
        calls.append((container, script, stdin))
        a = answers[container]
        return a(script, stdin) if callable(a) else a
    return run, calls


every = {"nidan-openmrs-db": (0, "0"), "nidan-odoo-db": (0, "1\n$pbkdf2-sha512$x"), "nidan-odoo": (0, "ok"),
         "nidan-openelis-db": (0, "BEGIN\nCREATE EXTENSION\n0\nROLLBACK"), "nidan-superset": (0, "ok"),
         "nidan-orthanc": (0, json.dumps({"nidan": "Long-Random-Secret-1", "nidan-viewer": "Other-Random-2"}))}
run, calls = fake(every)
assert ds.evaluate(run, lambda c: True, words) == {a: "ok" for a in ds.APPS}
# nothing sensitive on a command line: demo words and hashes go on stdin only
for container, script, stdin in calls:
    assert "Admin123" not in script and "$pbkdf2-sha512$x" not in script, (container, script)
assert any("Admin123" in stdin for _, _, stdin in calls)

# one weak app, one that cannot be checked, one not deployed, one skipped by the operator
answers = dict(every, **{"nidan-openmrs-db": (0, "1"), "nidan-openelis-db": (1, "function crypt(unknown) does not exist")})
run, _ = fake(answers)
r = ds.evaluate(run, lambda c: c != "nidan-superset", words, skip={"orthanc"})
assert r["openmrs"] == "weak" and r["openelis"].startswith("error") and r["superset"] == "absent"
assert r["orthanc"] == "skipped" and r["odoo"] == "ok", r
# orthanc: a registered demo password is weak
run, _ = fake(dict(every, **{"nidan-orthanc": (0, json.dumps({"nidan": "admin"}))}))
assert ds.evaluate(run, lambda c: True, words)["orthanc"] == "weak"
# odoo with no local admin at all is safe; a failed query is NOT (it used to read as "no admin" = ok)
run, _ = fake(dict(every, **{"nidan-odoo-db": (0, "0")}))
assert ds.evaluate(run, lambda c: True, words)["odoo"] == "ok"
run, _ = fake(dict(every, **{"nidan-odoo-db": (0, "")}))
assert ds.evaluate(run, lambda c: True, words)["odoo"].startswith("error"), "empty answer must not be safe"
run, _ = fake(dict(every, **{"nidan-odoo-db": (3, 'ERROR:  relation "res_users" does not exist')}))
assert ds.evaluate(run, lambda c: True, words)["odoo"].startswith("error")
# openelis: the count is read out of psql's BEGIN / CREATE EXTENSION / ROLLBACK lines, and the extension is never kept
run, calls = fake(dict(every, **{"nidan-openelis-db": (0, "BEGIN\nCREATE EXTENSION\n1\nROLLBACK")}))
assert ds.evaluate(run, lambda c: True, words)["openelis"] == "weak"
sql = [stdin for c, _, stdin in calls if c == "nidan-openelis-db"][0]
assert sql.startswith("BEGIN;") and sql.rstrip().endswith("ROLLBACK;"), sql[:40]
# quoting: a quote in a demo word cannot end the SQL literal
assert ds.sql_lit("it's") == "'it''s'" and ds.pg_lit("it's") == "'it''s'"

# --apply edits only NIDAN_SSO_ENABLED in .env.local
with tempfile.TemporaryDirectory() as d:
    f = Path(d) / ".env.local"
    f.write_text("A=1\nNIDAN_SSO_ENABLED=true\nOPENMRS_SSO_ENABLED=true\nOPENMRS_AUTH_MODE=jwt\nDHIS2_AUTH_PROVIDER=oidc\nB=2\n")
    f.chmod(0o600)
    ds.set_sso_off(f)
    assert f.read_text() == ("A=1\nNIDAN_SSO_ENABLED=false\nOPENMRS_SSO_ENABLED=\nOPENMRS_AUTH_MODE=basic\n"
                             "DHIS2_AUTH_PROVIDER=env_file\nB=2\n"), f.read_text()
    assert oct(f.stat().st_mode & 0o777) == "0o600" and not (Path(d) / ".env.local.tmp").exists()
    g = Path(d) / "new"
    ds.set_sso_off(g)
    assert g.read_text() == "NIDAN_SSO_ENABLED=false\nOPENMRS_AUTH_MODE=basic\n", g.read_text()
print("disable-sso self-test passed")
