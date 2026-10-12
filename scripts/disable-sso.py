#!/usr/bin/env python3
"""Switch central login off only when every app's own admin login is safe (backlog KC-29-03).

  scripts/disable-sso.py            check only (exit 1 while any app is unsafe or cannot be checked)
  scripts/disable-sso.py --apply    check, then set NIDAN_SSO_ENABLED=false in .env.local
        [--skip APP ...]            leave an app out (logged; for an app this site does not run)

With central login off, every app is back on its own local login, so each app's local admin password must not be
a demo or default value. Checked, read only, in each app's own storage (nothing leaves the containers but a yes/no):
  openmrs   users admin           SHA-512(password + salt), compared in MariaDB
  odoo      res_users login admin  Odoo's own passlib, in the odoo container (hash read from odoo-db)
  openelis  login_user admin       bcrypt via pgcrypto crypt() in openelis-db (extension created in a
                                   rolled-back transaction: nothing is left behind)
  superset  ab_user (SUPERSET_ADMIN_USERNAME)  werkzeug, in the superset container
  orthanc   every password in the running container's ORTHANC__REGISTERED_USERS
"Demo or default" = Admin123, admin, every *PASSWORD value in the tracked .env, and keycloak/password-blocklist.txt.
An app whose container is not on this host is skipped; one that cannot be checked counts as unsafe. For each unsafe
app a reset slip is printed. Demo passwords and hashes reach the containers on stdin, never on a command line.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APPS = ("openmrs", "odoo", "openelis", "superset", "orthanc")

SLIPS = {
    "openmrs": "OpenMRS: sign in as admin (legacy UI /openmrs/admin) -> My Profile -> Change Password; 16+ characters.",
    "odoo": "Odoo: sign in as admin -> Preferences -> Account Security -> Change Password; 16+ characters.",
    "openelis": "OpenELIS: sign in as admin -> Admin -> User Management -> admin -> new password; 16+ characters.",
    "superset": "Superset: superset fab reset-password --username <SUPERSET_ADMIN_USERNAME> (in the superset container).",
    "orthanc": "Orthanc: set new ORTHANC_PASSWORD / ORTHANC_VIEWER_PASSWORD in .env.local (generate-secrets.sh) and "
               "recreate orthanc and the gateway.",
}

CONTAINERS = {"openmrs": ["nidan-openmrs-db"], "odoo": ["nidan-odoo-db", "nidan-odoo"], "openelis": ["nidan-openelis-db"],
              "superset": ["nidan-superset"], "orthanc": ["nidan-orthanc"]}


def demo_words():
    words = ["Admin123", "admin"]
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Z0-9_]*PASSWORD[A-Z0-9_]*)\s*=\s*(.*)$", line)
        if m and m.group(2).strip().strip("'\""):
            words.append(m.group(2).strip().strip("'\""))
    words += [w.strip() for w in (ROOT / "keycloak" / "password-blocklist.txt").read_text(encoding="utf-8").splitlines()
              if w.strip() and not w.startswith("#")]
    return list(dict.fromkeys(words))


def docker(container, script, stdin=""):
    """(returncode, stdout) of `sh -c script` in the container; stdin carries anything sensitive."""
    r = subprocess.run(["docker", "exec", "-i", container, "sh", "-c", script], input=stdin, capture_output=True,
                       text=True)
    return r.returncode, r.stdout.strip() + ("\n" + r.stderr.strip() if r.returncode else "")


def present(container):
    return subprocess.run(["docker", "inspect", container], capture_output=True).returncode == 0


def sql_lit(w):
    return "'" + w.replace("\\", "\\\\").replace("'", "''") + "'"


def pg_lit(w):
    return "'" + w.replace("'", "''") + "'"


def check_openmrs(run, words):
    tests = " OR ".join(f"password = SHA2(CONCAT({sql_lit(w)}, salt), 512)" for w in words)
    rc, out = run("nidan-openmrs-db", 'MYSQL_PWD="$MYSQL_PASSWORD" exec mariadb -N -u"$MYSQL_USER" "$MYSQL_DATABASE"',
                  f"SELECT COUNT(*) FROM users WHERE (system_id = 'admin' OR username = 'admin') AND ({tests});")
    return ("weak" if out.splitlines()[0].strip() != "0" else "ok") if rc == 0 and out else f"error: {out[:200]}"


ODOO_VERIFY = ("import sys, json; from passlib.context import CryptContext; d = json.load(sys.stdin); "
               "c = CryptContext(['pbkdf2_sha512', 'plaintext'], deprecated=['plaintext']); "
               "print('weak' if d['hash'] and any(c.verify(w, d['hash']) for w in d['words']) else 'ok')")


def check_odoo(run, words):
    # ON_ERROR_STOP: psql reading stdin otherwise exits 0 on a failed query, and the empty answer would read as "no
    # local admin password" = safe (found live on the harness, 2026-10-10: wrong database -> "ok").
    rc, h = run("nidan-odoo-db", 'exec psql -tA -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "${ODOO_DB_NAME:-$POSTGRES_DB}"',
                "SELECT COUNT(*) FROM res_users WHERE login = 'admin'; SELECT password FROM res_users WHERE login = 'admin';")
    lines = h.splitlines()
    if rc != 0 or not lines or not lines[0].strip().isdigit():
        return f"error: {h[:200]}"
    if lines[0].strip() == "0" or len(lines) < 2 or not lines[1].strip():
        return "ok"   # no local admin login, or one without a password
    rc, out = run("nidan-odoo", f'exec python3 -c "{ODOO_VERIFY}"', json.dumps({"hash": lines[1].strip(), "words": words}))
    return out if rc == 0 and out in ("weak", "ok") else f"error: {out[:200]}"


def check_openelis(run, words):
    # OpenELIS hashes with bcrypt and its DB has no pgcrypto: create it inside a transaction that is rolled back
    # (pgcrypto is a trusted extension since PostgreSQL 13, so the DB owner may), so nothing is left behind.
    tests = " OR ".join(f"password = crypt({pg_lit(w)}, password)" for w in words)
    rc, out = run("nidan-openelis-db", 'exec psql -tA -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
                  "BEGIN; CREATE EXTENSION IF NOT EXISTS pgcrypto; "
                  f"SELECT COUNT(*) FROM clinlims.login_user WHERE login_name = 'admin' AND ({tests}); ROLLBACK;")
    counts = [line for line in out.splitlines() if line.strip().isdigit()]
    return ("weak" if counts[0].strip() != "0" else "ok") if rc == 0 and counts else f"error: {out[:200]}"


SUPERSET_VERIFY = ("import sys, json, os, sqlite3; from werkzeug.security import check_password_hash; d = json.load(sys.stdin); "
                   "u = os.environ.get('SUPERSET_ADMIN_USERNAME', 'admin'); "
                   "r = sqlite3.connect('file:/app/superset_home/superset.db?mode=ro', uri=True).execute("
                   "'SELECT password FROM ab_user WHERE username = ?', (u,)).fetchone(); "
                   "print('weak' if r and r[0] and any(check_password_hash(r[0], w) for w in d['words']) else 'ok')")


def check_superset(run, words):
    rc, out = run("nidan-superset", f'exec python -c "{SUPERSET_VERIFY}"', json.dumps({"words": words}))
    return out if rc == 0 and out in ("weak", "ok") else f"error: {out[:200]}"


def check_orthanc(run, words):
    # every registered user's password (ORTHANC__REGISTERED_USERS, as compose renders it); compared here, in memory
    rc, out = run("nidan-orthanc", 'printenv ORTHANC__REGISTERED_USERS')
    if rc != 0 or not out:
        return f"error: {out[:200] or 'no ORTHANC__REGISTERED_USERS'}"
    try:
        users = json.loads(out)
    except ValueError:
        return "error: ORTHANC__REGISTERED_USERS is not JSON"
    return "weak" if any(v in words for v in users.values()) else "ok"


CHECKS = {"openmrs": check_openmrs, "odoo": check_odoo, "openelis": check_openelis, "superset": check_superset,
          "orthanc": check_orthanc}


def evaluate(run, is_present, words, skip=()):
    """{app: 'ok' | 'weak' | 'absent' | 'skipped' | 'error: ...'}"""
    result = {}
    for app in APPS:
        if app in skip:
            result[app] = "skipped"
        elif not all(is_present(c) for c in CONTAINERS[app]):
            result[app] = "absent"
        else:
            try:
                result[app] = CHECKS[app](run, words)
            except Exception as e:  # a check that cannot run counts as unsafe
                result[app] = f"error: {e}"
    return result


# Central login off = every switch the cutover turned on goes back (runbook step 7): per-app overrides follow
# NIDAN_SSO_ENABLED again (empty), CIS and Odoo call OpenMRS with Basic, the DHIS2 app uses its own login.
ROLLBACK = {"NIDAN_SSO_ENABLED": "false", "OPENMRS_SSO_ENABLED": "", "OPENELIS_SSO_ENABLED": "",
            "SUPERSET_SSO_ENABLED": "", "OPENMRS_AUTH_MODE": "basic", "DHIS2_AUTH_PROVIDER": "env_file"}


def set_sso_off(env_file):
    """Rewrite those keys in .env.local (others untouched), atomically: a full disk cannot truncate the secrets file."""
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    out, seen = [], set()
    for line in lines:
        m = re.match(r"\s*([A-Z0-9_]+)\s*=", line)
        if m and m.group(1) in ROLLBACK:
            out.append(f"{m.group(1)}={ROLLBACK[m.group(1)]}")
            seen.add(m.group(1))
        else:
            out.append(line)
    # must be explicit: with keycloak.docker-compose.yml an absent one means on / jwt / oidc; the per-app overrides
    # follow NIDAN_SSO_ENABLED when absent
    for key in ("NIDAN_SSO_ENABLED", "OPENMRS_AUTH_MODE", "DHIS2_AUTH_PROVIDER"):
        if key not in seen:
            out.append(f"{key}={ROLLBACK[key]}")
    tmp = env_file.with_name(env_file.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    os.replace(tmp, env_file)


def main(argv):
    args = list(argv[1:])
    apply = "--apply" in args
    skip = set()
    while "--skip" in args:
        i = args.index("--skip")
        skip.add(args[i + 1] if i + 1 < len(args) else "")
        del args[i:i + 2]
    args = [a for a in args if a != "--apply"]
    if args or not skip <= set(APPS):
        print(__doc__)
        return 2
    result = evaluate(docker, present, demo_words(), skip)
    unsafe = [a for a, r in result.items() if r == "weak" or r.startswith("error")]
    for app, r in result.items():
        print(f"{app:9s} {r}" + ("   (SKIPPED BY OPERATOR)" if r == "skipped" else ""))
    if unsafe:
        print("\nCentral login stays ON. Reset these local admin passwords first, then run this again:")
        for app in unsafe:
            print(f"  [{app}] " + (SLIPS[app] if result[app] == "weak" else f"could not check ({result[app]}); "
                                   "fix the check or reset the password and check by hand. " + SLIPS[app]))
        return 1
    if not apply:
        print("\nall local admin logins are safe; run again with --apply to switch central login off")
        return 0
    set_sso_off(ROOT / ".env.local")
    print("\ncentral login switched off in .env.local (NIDAN_SSO_ENABLED=false, per-app overrides cleared, "
          "OPENMRS_AUTH_MODE=basic, DHIS2_AUTH_PROVIDER=env_file).\nApply it to every service, including openmrs-frontend: "
          "docker compose up -d   (runbook: Rolling back)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
