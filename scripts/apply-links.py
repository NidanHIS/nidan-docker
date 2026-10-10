#!/usr/bin/env python3
"""Apply the reviewed link table at a site cutover (backlog KC-36, spec D22).

  scripts/apply-links.py plan  EXPORT.json WORKBOOK_DIR          validate + write WORKBOOK_DIR/link-plan.sql
  scripts/apply-links.py apply EXPORT.json WORKBOOK_DIR SHA256   run that plan (SHA256 = its printed digest)
  scripts/apply-links.py lock-plan  EXPORT.json WORKBOOK_DIR [--only USERNAMES.txt]
                                                                 write WORKBOOK_DIR/password-lock.sql (KC-36-03)
  scripts/apply-links.py lock-apply EXPORT.json WORKBOOK_DIR SHA256  run that file as reviewed

Every app refuses a central-login user whose account is not linked by Keycloak sub (OpenMRS KC-58-04/05,
Odoo KC-37, OpenELIS KC-38). This writes the link keys for the rows marked decision=link:
  openmrs   users.system_id         = sub
  odoo      res_users.oauth_uid     = sub, oauth_provider_id = the "Nidan central login" provider
  openelis  system_user.external_id = sub
Superset (links by username + local-account refusal) and the DHIS2 app (no stored users for central
login) need no link key: reported, not written.

Refuses to plan when: a keycloak_sub is not a UUID; an app account is linked to two Keycloak users, or a
Keycloak user to two accounts of one app (KC-36-02); a linked account is not in the export; an app export
failed. Each statement also checks the account still has the exported username, so a renamed account is
not linked by id alone. `apply` runs each app's statements in one transaction and only when the plan
file's sha256 matches the digest the operator reviewed.
"""
import base64
import csv
import hashlib
import importlib.util
import json
import re
import secrets
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("bootstrap_export", HERE / "bootstrap-export.py")
be = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(be)

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
USERNAME = re.compile(r"[A-Za-z0-9._@-]{1,100}")
LINKED_APPS = ("openmrs", "odoo", "openelis")
NO_KEY_APPS = {"superset": "links by username; local accounts are refused (KC-39-03)",
               "dhis2": "central-login users are not stored (KC-40)"}


def sql_for(app, user_id, username, sub):
    """One UPDATE that sets the link key only if the account still has the exported username."""
    if app == "openmrs":
        return (f"UPDATE users SET system_id = '{sub}' WHERE user_id = {user_id} AND username = '{username}' "
                f"AND NOT EXISTS (SELECT 1 FROM (SELECT user_id FROM users WHERE system_id = '{sub}' "
                f"AND user_id <> {user_id}) other);")
    if app == "odoo":
        return (f"UPDATE res_users SET oauth_uid = '{sub}', oauth_provider_id = (SELECT id FROM auth_oauth_provider "
                f"WHERE nidan_keycloak LIMIT 1) WHERE id = {user_id} AND login = '{username}';")
    if app == "openelis":
        return (f"UPDATE clinlims.system_user SET external_id = '{sub}' WHERE id = {user_id} "
                f"AND login_name = '{username}' AND NOT EXISTS (SELECT 1 FROM clinlims.system_user other "
                f"WHERE other.external_id = '{sub}' AND other.id <> {user_id});")
    raise ValueError(app)


def _odoo_unusable_hash():
    """A valid passlib pbkdf2-sha512 hash of a random 256-bit secret nobody knows (Odoo verifies with passlib;
    an empty or malformed hash could raise instead of failing cleanly)."""
    def ab64(raw):
        return base64.b64encode(raw).decode().rstrip("=").replace("+", ".")
    salt, rounds = secrets.token_bytes(16), 25000
    digest = hashlib.pbkdf2_hmac("sha512", secrets.token_bytes(32), salt, rounds)
    return f"$pbkdf2-sha512${rounds}${ab64(salt)}${ab64(digest)}"


def lock_sql_for(app, user_id, sub):
    """Make the local password of a LINKED account unusable (KC-36-03). Only touches the account if it is
    linked to this sub, so an unlinked or relinked account is never locked by mistake."""
    if app == "openmrs":
        return (f"UPDATE users SET password = '{secrets.token_hex(64)}', salt = '{secrets.token_hex(64)}' "
                f"WHERE user_id = {user_id} AND system_id = '{sub}';")
    if app == "odoo":
        return (f"UPDATE res_users SET password = '{_odoo_unusable_hash()}' WHERE id = {user_id} "
                f"AND oauth_uid = '{sub}';")
    if app == "openelis":
        return (f"UPDATE clinlims.login_user SET password = '{secrets.token_hex(32)}' WHERE login_name = "
                f"(SELECT login_name FROM clinlims.system_user WHERE id = {user_id} AND external_id = '{sub}');")
    raise ValueError(app)


def check_for(app, user_id, sub):
    """A read-only query that returns a row only when the link is NOT in place."""
    if app == "openmrs":
        return (f"SELECT 'NOT-LINKED', {user_id} FROM DUAL WHERE NOT EXISTS "
                f"(SELECT 1 FROM users WHERE user_id = {user_id} AND system_id = '{sub}');")
    if app == "odoo":
        return (f"SELECT 'NOT-LINKED', {user_id} WHERE NOT EXISTS (SELECT 1 FROM res_users WHERE id = {user_id} "
                f"AND oauth_uid = '{sub}' AND oauth_provider_id IS NOT NULL);")
    return (f"SELECT 'NOT-LINKED', {user_id} WHERE NOT EXISTS (SELECT 1 FROM clinlims.system_user "
            f"WHERE id = {user_id} AND external_id = '{sub}');")


def verify(links, run=subprocess.run):
    """[(app, user_id)] of planned links that are not in place, checked read-only."""
    missing = []
    for app in LINKED_APPS:
        rows = [check_for(app, uid, sub) for a, uid, sub in links if a == app]
        if not rows:
            continue
        a = be.APPS[app]
        sql = "\n".join([a["begin"], *rows, a["end"]]) + "\n"
        res = run(["docker", "exec", "-i", a["container"], "sh", "-c", a["shell"]], input=sql,
                  capture_output=True, text=True)
        if res.returncode != 0:
            missing += [(app, "verification failed: " + (res.stderr or "").strip()[:200])]
            continue
        missing += [(app, line.split("\t")[1]) for line in res.stdout.splitlines() if line.startswith("NOT-LINKED")]
    return missing


def plan(export, link_rows):
    """(statements per app, [(app, user_id, sub)] planned links, problems, notes)."""
    problems, notes = [], []
    by_account, by_sub_app = {}, {}
    out = {app: [] for app in LINKED_APPS}
    links = []
    for app in LINKED_APPS:
        if "error" in (export.get(app) or {}):
            problems.append(f"{app}: export failed, cannot verify accounts")
    exported = {app: {str(u["id"]): u for u in (export.get(app) or {}).get("users", [])} for app in export}
    for n, r in enumerate(link_rows, 2):
        if (r.get("decision") or "").strip().lower() != "link":
            continue
        app, uid = r.get("app", ""), str(r.get("app_user_id", "")).strip()
        sub = (r.get("keycloak_sub") or "").strip().lower()
        where = f"link-table.csv:{n} {app}:{uid}"
        if app in NO_KEY_APPS:
            notes.append(f"{where}: {app} needs no link key ({NO_KEY_APPS[app]})")
            continue
        if app not in LINKED_APPS:
            problems.append(f"{where}: unknown app")
            continue
        if not UUID.fullmatch(sub):
            problems.append(f"{where}: keycloak_sub {sub!r} is not a UUID")
            continue
        if not uid.isdigit():
            problems.append(f"{where}: app_user_id is not numeric")
            continue
        account = exported.get(app, {}).get(uid)
        if not account:
            problems.append(f"{where}: account not in the export (deleted or wrong id?)")
            continue
        if not USERNAME.fullmatch(str(account["username"])):
            problems.append(f"{where}: exported username has unexpected characters; link it by hand")
            continue
        by_account.setdefault((app, uid), set()).add(sub)
        by_sub_app.setdefault((sub, app), set()).add(uid)
        out[app].append(sql_for(app, uid, account["username"], sub))
        links.append((app, uid, sub))
    for (app, uid), subs in sorted(by_account.items()):
        if len(subs) > 1:
            problems.append(f"{app}:{uid} is linked to {len(subs)} Keycloak users (KC-36-02)")
    for (sub, app), uids in sorted(by_sub_app.items()):
        if len(uids) > 1:
            problems.append(f"Keycloak user {sub} is linked to {len(uids)} {app} accounts ({', '.join(sorted(uids))})")
    return out, links, problems, notes


def render(statements):
    lines = ["-- Link plan (scripts/apply-links.py). Review, then apply with its sha256.\n"]
    for app in LINKED_APPS:
        lines.append(f"-- @app {app} ({len(statements[app])} statement(s))\n")
        lines += [s + "\n" for s in statements[app]]
    return "".join(lines)


def parse_plan(text):
    plan_by_app, app = {a: [] for a in LINKED_APPS}, None
    for line in text.splitlines():
        m = re.match(r"-- @app (\w+)", line)
        if m:
            app = m.group(1)
        elif line.strip() and not line.startswith("--") and app:
            plan_by_app[app].append(line.strip())
    return plan_by_app


def apply(plan_text, run=subprocess.run):
    """Run each app's statements in one transaction (fails as a whole per app)."""
    results = {}
    for app, stmts in parse_plan(plan_text).items():
        if not stmts:
            continue
        a = be.APPS[app]
        begin = "START TRANSACTION;" if "mysql" in a["shell"].lower() else "BEGIN;"
        sql = "\n".join([begin, *stmts, "COMMIT;"]) + "\n"
        res = run(["docker", "exec", "-i", a["container"], "sh", "-c", a["shell"]], input=sql,
                  capture_output=True, text=True)
        results[app] = (res.returncode, (res.stderr or "").strip()[:300])
    return results


def lock(argv, export, wb, rows):
    """lock-plan / lock-apply: the random values are generated ONCE into the file; apply runs that file."""
    _, links, problems, _ = plan(export, rows)
    if problems:
        print(f"refusing: {len(problems)} problem(s) in the link table (fix them before locking)")
        return 1
    target = wb / "password-lock.sql"
    if argv[1] == "lock-plan":
        only = None
        if "--only" in argv:
            only = {l.strip().lower() for l in Path(argv[argv.index("--only") + 1]).read_text().splitlines() if l.strip()}
        names = {app: {str(u["id"]): str(u["username"]).lower() for u in (export.get(app) or {}).get("users", [])}
                 for app in LINKED_APPS}
        chosen = [(a, u, sub) for a, u, sub in links if only is None or names[a].get(u) in only]
        statements = {app: [lock_sql_for(a, u, sub) for a, u, sub in chosen if a == app] for app in LINKED_APPS}
        text = render(statements).replace("Link plan", "Password lock plan")
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target}: " + ", ".join(f"{a} {len(statements[a])}" for a in LINKED_APPS))
        print(f"review it, then: scripts/apply-links.py lock-apply {argv[2]} {argv[3]} "
              f"{hashlib.sha256(text.encode()).hexdigest()}")
        return 0
    text = target.read_text(encoding="utf-8")
    if len(argv) != 5 or argv[4] != hashlib.sha256(text.encode()).hexdigest():
        print("refusing: the sha256 given does not match password-lock.sql")
        return 1
    failed = False
    for app, (code, err) in apply(text).items():
        print(f"{app}: {'ok' if code == 0 else 'FAILED (rolled back) ' + err}")
        failed |= code != 0
    return 1 if failed else 0


def main(argv):
    if len(argv) < 4 or argv[1] not in ("plan", "apply", "lock-plan", "lock-apply"):
        print(__doc__)
        return 2
    export = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    wb = Path(argv[3])
    if be.inside_git(wb):
        raise SystemExit("refusing to work inside a git work tree (the workbook holds staff names)")
    rows = list(csv.DictReader(open(wb / "link-table.csv", newline="", encoding="utf-8")))
    if argv[1].startswith("lock-"):
        return lock(argv, export, wb, rows)
    statements, links, problems, notes = plan(export, rows)
    for n in notes:
        print("  note: " + n)
    if problems:
        print(f"refusing: {len(problems)} problem(s)")
        for p in problems:
            print("  " + p)
        return 1
    text = render(statements)
    digest = hashlib.sha256(text.encode()).hexdigest()
    if argv[1] == "plan":
        (wb / "link-plan.sql").write_text(text, encoding="utf-8")
        print(f"wrote {wb / 'link-plan.sql'}: " + ", ".join(f"{a} {len(statements[a])}" for a in LINKED_APPS))
        print(f"review it, then: scripts/apply-links.py apply {argv[2]} {argv[3]} {digest}")
        return 0
    if len(argv) != 5 or argv[4] != digest:
        print("refusing: the sha256 given does not match the plan that the current link table produces")
        return 1
    on_disk = (wb / "link-plan.sql").read_text(encoding="utf-8")
    if on_disk != text:
        print("refusing: link-plan.sql on disk differs from the reviewed plan")
        return 1
    failed = False
    for app, (code, err) in apply(text).items():
        print(f"{app}: {'ok' if code == 0 else 'FAILED (rolled back) ' + err}")
        failed |= code != 0
    missing = verify(links)
    for app, uid in missing:
        print(f"  NOT LINKED: {app}:{uid} (renamed, already taken by another account, or failed)")
    return 1 if failed or missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
