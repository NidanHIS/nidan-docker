"""Self-test for app-db-roles.py: `python3 -I scripts/test_app_db_roles.py` (no Docker needed)."""
import contextlib
import importlib.util
import io
from pathlib import Path

spec = importlib.util.spec_from_file_location("adr", Path(__file__).resolve().parent / "app-db-roles.py")
adr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adr)

PW = "p" * 40
calls = []


def fake_runner(cfg, role, password, name):
    calls.append((cfg["prefix"], role, password, name))
    return 0, "nidan_app | f | f | f"


def run(argv, env):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = adr.main(["x", *argv], environ=env, runner=fake_runner)
    return rc, out.getvalue()


# no password -> refused, nothing runs
rc, out = run(["--apply", "integration"], {})
assert rc == 1 and "INTEGRATION_DB_APP_PASSWORD must be set" in out and not calls, out
# a role name that is not a plain identifier is refused
rc, out = run(["integration"], {"INTEGRATION_DB_APP_USER": "x; drop", "INTEGRATION_DB_APP_PASSWORD": PW})
assert rc == 1 and "letters, digits and _ only" in out, out
# a password that could break psql quoting is refused
rc, out = run(["integration"], {"INTEGRATION_DB_APP_PASSWORD": "a'" + "b" * 40})
assert rc == 1 and "A-Z a-z 0-9" in out, out
# dry run: SQL printed, the password never
rc, out = run(["integration"], {"INTEGRATION_DB_APP_PASSWORD": PW})
assert rc == 0 and PW not in out and "'********'" in out and "NOSUPERUSER" in out and not calls, out
# apply: runner gets the default role, the password and the container override; output names no password
rc, out = run(["--apply", "integration"], {"INTEGRATION_DB_APP_PASSWORD": PW, "INTEGRATION_DB_CONTAINER": "harness-db"})
assert rc == 0 and calls == [("INTEGRATION", "nidan_app", PW, "harness-db")] and PW not in out, (calls, out)
assert "INTEGRATION_DB_APP_USER=nidan_app" in out
# unknown database name -> usage
assert run(["mysql"], {})[0] == 2
print("app-db-roles: OK")
