"""Self-test for incident.py: `python3 -I scripts/test_incident.py`."""
import importlib.util
import os
import stat
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("inc", HERE / "incident.py")
inc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inc)

COMPONENTS = ("id-def,rsa-generated\nid-enc,rsa-enc-generated\nid-hmac,hmac-generated\n"
              "id-old,rsa-20261001120000\nid-new,rsa-20261008120000\nid-aes,aes-generated\n")


def run_kc_script(argv, components):
    """Run the sh -c script of a kc() step with a stub kcadm.sh; return the components it disabled."""
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "updates"
        stub = Path(d) / "kcadm.sh"
        stub.write_text("#!/bin/sh\ncase \"$1\" in\n  config) exit 0 ;;\n"
                        f"  get) printf '%s' '{components}' ;;\n"
                        f"  update) echo \"$2\" >> {log} ;;\nesac\n")
        stub.chmod(0o755)
        script = argv[-1].replace(inc.KCADM, str(stub))
        res = subprocess.run(["sh", "-c", script], capture_output=True, text=True,
                             env=dict(os.environ, KC_AUTOMATION_CLIENT_ID="a", KC_AUTOMATION_CLIENT_SECRET="b"))
        return res.returncode, (log.read_text().split() if log.exists() else []), res.stderr


def test_retire_old_keeps_newest_signing_key_and_never_touches_enc_or_hmac():
    (_, step, _), = inc.plan("rotate-realm-keys", ["--retire-old"])
    code, disabled, err = run_kc_script(step, COMPONENTS)
    assert code == 0, err
    assert sorted(disabled) == ["components/id-def", "components/id-old"], disabled


def test_retire_old_refuses_before_any_rotation():
    (_, step, _), = inc.plan("rotate-realm-keys", ["--retire-old"])
    code, disabled, err = run_kc_script(step, "id-def,rsa-generated\nid-enc,rsa-enc-generated\n")
    assert code != 0 and disabled == [] and "run rotate-realm-keys first" in err


def test_secrets_never_on_the_command_line():
    for command, args in (("revoke-all", []), ("rotate-client-secret", ["cis"]), ("rotate-realm-keys", [])):
        for _, step, extra in inc.plan(command, args):
            if isinstance(step, list):
                joined = " ".join(step)
                for value in extra.values():
                    assert value not in joined, (command, "secret on argv")
                assert "$KC_AUTOMATION_CLIENT_SECRET" in joined or step[0] == "docker" and "exec" not in step


def test_client_secret_rotation_targets():
    steps = inc.plan("rotate-client-secret", ["odoo-sync"])
    assert any(s[1] == ("setenv", "KC_ODOO_SYNC_CLIENT_SECRET", steps[0][2]["NEW_CLIENT_SECRET"]) for s in steps)
    assert steps[-1][1][-1] == "odoo"
    try:
        inc.plan("rotate-client-secret", ["admin-cli"])
        raise AssertionError("unknown client accepted")
    except SystemExit:
        pass


def test_set_env_key_backs_up_and_replaces():
    with tempfile.TemporaryDirectory() as d:
        env = Path(d) / ".env.local"
        env.write_text("A=1\nOAUTH2_PROXY_COOKIE_SECRET=old\nB=2\n")
        backup = inc.set_env_key("OAUTH2_PROXY_COOKIE_SECRET", "new", env)
        assert env.read_text() == "A=1\nOAUTH2_PROXY_COOKIE_SECRET=new\nB=2\n"
        assert backup.read_text().count("=old") == 1
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600 and stat.S_IMODE(env.stat().st_mode) == 0o600
        inc.set_env_key("NEW_KEY", "x", env)
        assert env.read_text().endswith("NEW_KEY=x\n")


def test_dry_run_runs_nothing_and_execute_needs_the_automation_client():
    calls = []
    real = subprocess.run
    inc.subprocess.run = lambda *a, **k: calls.append(a) or real(["true"])
    try:
        assert inc.main(["x", "revoke-all"]) == 0 and calls == []
        env = {k: v for k, v in os.environ.items() if not k.startswith("KC_AUTOMATION")}
        old = os.environ.copy()
        os.environ.clear()
        os.environ.update(env)
        try:
            assert inc.main(["x", "revoke-all", "--execute"]) == 2 and calls == []
        finally:
            os.environ.clear()
            os.environ.update(old)
    finally:
        inc.subprocess.run = real


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
    print(f"incident: {n} tests passed")
