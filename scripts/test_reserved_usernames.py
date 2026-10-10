"""Self-test for reserved-usernames.py: run with `python3 -I scripts/test_reserved_usernames.py`.

Also runs the pattern through java.util.regex (Keycloak's engine) when a JDK 11+ `java` is found.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ru", HERE / "reserved-usernames.py")
ru = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ru)

TRACKED_ENVS = [str(HERE.parent / ".env"), str(HERE.parent / "env.template")]
# KC-55-03 list, plus a local-only site name and shapes
REFUSE = ["admin", "nidan", "nidan-sync", "daemon", "__system__", "public", "default", "cis", "odoo-sync",
          "portal-sync", "nidan-gateway", "cis-worker", "ois-replay", "service-account-cis", "3-4", "123-9",
          "6f1c2a9e-0b7d-4c1e-9f3a-2d5e8b7c6a10", "localadm", "ab", "Ram", "ram@x", "9lives", "a" * 41]
ACCEPT = ["ram", "sita.k", "nurse_01", "dr-shrestha", "admins", "nidan2", "cisco", "adminx", "localadmin"]


def build(site=("localadm",)):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("\n".join(site) + "\n")
    try:
        names = ru.reserved(TRACKED_ENVS, f.name)
    finally:
        os.unlink(f.name)
    return names, ru.pattern(names)


def test_python_semantics():
    names, pat = build()
    for n in REFUSE:
        assert not re.fullmatch(pat, n), f"accepted reserved {n!r}"
    for n in ACCEPT:
        assert re.fullmatch(pat, n), f"refused normal {n!r}"


def test_every_env_username_is_reserved():
    # KC-55-04: each *_USERNAME / *_USER value in the tracked env files is refused
    names, pat = build(site=())
    env = ru.env_names(TRACKED_ENVS)
    assert "nidan-sync" in env and len(env) > 5, env
    for n in env:
        if re.fullmatch(r"[a-z][a-z0-9._-]{2,39}", n):
            assert not re.fullmatch(pat, n), f"env username {n!r} not reserved"


def test_no_backslash_and_json_safe():
    _, pat = build()
    assert "\\" not in pat and '"' not in pat and "$(" not in pat
    json.loads(json.dumps({"p": pat}))


def test_cli_doubles_dollar_for_compose():
    out = subprocess.run(["python3", "-I", str(HERE / "reserved-usernames.py"), "--env-file", TRACKED_ENVS[0]],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out.startswith("KC_USERNAME_PATTERN=^") and out.endswith("$$") and "$$)" in out, out[-40:]


def test_realm_lint_refuses_a_weakened_profile():
    cs = importlib.util.spec_from_file_location("cr", HERE / "check-realm.py")
    cr = importlib.util.module_from_spec(cs)
    cs.loader.exec_module(cr)
    doc = ru.yaml.safe_load((HERE.parent / "keycloak/realm/base/nidan.yaml").read_text())
    assert cr.check_profile(doc, "base") == []
    box = doc["components"]["org.keycloak.userprofile.UserProfileProvider"][0]["config"]["kc.user.profile.config"]
    cfg = json.loads(box[0])
    for breakit in ("pattern", "edit", "unmanaged"):
        bad = json.loads(json.dumps(cfg))
        if breakit == "pattern":
            del bad["attributes"][0]["validations"]["pattern"]
        elif breakit == "edit":
            bad["attributes"][-1]["permissions"]["edit"] = ["admin", "user"]
        else:
            bad["unmanagedAttributePolicy"] = "ENABLED"
        box[0] = json.dumps(bad)
        assert cr.check_profile(doc, "base"), breakit
    assert cr.check_profile({"realm": "nidan"}, "base"), "missing profile must fail"


def java():
    home = subprocess.run(["/usr/libexec/java_home", "-v", "11+"], capture_output=True, text=True).stdout.strip() \
        if Path("/usr/libexec/java_home").exists() else ""
    j = Path(home, "bin", "java") if home else shutil.which("java")
    return str(j) if j and Path(j).exists() else None


def test_java_regex_agrees():
    j = java()
    if not j:
        print("  (no JDK 11+: java check skipped)")
        return
    _, pat = build()
    src = ("import java.util.regex.Pattern;public class R{public static void main(String[] a){"
           "Pattern p=Pattern.compile(a[0]);for(int i=1;i<a.length;i++)"
           "System.out.println(p.matcher(a[i]).matches());}}")
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "R.java").write_text(src)
        out = subprocess.run([j, str(Path(d) / "R.java"), pat, *REFUSE, *ACCEPT], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    got = out.stdout.split()
    assert got == ["false"] * len(REFUSE) + ["true"] * len(ACCEPT), list(zip(REFUSE + ACCEPT, got))


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
    print(f"reserved-usernames: {n} tests passed")
