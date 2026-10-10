#!/usr/bin/env python3
"""Bootstrap exporter + review workbook for a live site (backlog KC-25, spec §9.2 D41/D22).

  scripts/bootstrap-export.py export   OUT.json         read every app's users, roles, memberships
  scripts/bootstrap-export.py workbook OUT.json DIR     write/merge the review workbook into DIR

export: fixed SELECT-only queries, each app inside a READ ONLY transaction (the database itself
refuses writes, whatever account runs it; Superset's SQLite is opened mode=ro). Credentials stay
inside each DB container (its own env); nothing is written on the server.
workbook (DIR must be outside any git repo: it holds staff names):
  roles.csv            app, role, users, catalog (base | superuser | site-only), proposed class
  link-table.csv       one row per (username, app). decision/keycloak_sub start EMPTY: same-username
                       pairs are grouped for review, never auto-linked (KC-25-02). Re-running keeps
                       every decision already entered and never removes rows.
  reserved-usernames.txt  every local username whose rows are not all decision=link: Keycloak must
                       refuse these names (KC-25-03 -> KC-55-03), so nobody can claim a local account.
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
CATALOG_DIR = HERE.parent / "keycloak" / "realm" / "roles"

# Each row: kind<TAB>a<TAB>b<TAB>c<TAB>d. U = user (id, username, active 1/0, note),
# R = role (name, member count), M = membership (user id, role).
MYSQL = 'MYSQL_PWD="$MYSQL_PASSWORD" mysql -u"$MYSQL_USER" "$MYSQL_DATABASE" -B -N'
PSQL = 'psql -X -q -At -F "\t" -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d {db}'
APPS = {
    "openmrs": {
        "container": "nidan-openmrs-db", "shell": MYSQL, "begin": "START TRANSACTION READ ONLY;", "end": "COMMIT;",
        "queries": [
            "SELECT 'U', user_id, username, IF(retired, 0, 1), system_id FROM users WHERE username <> ''",
            "SELECT 'R', r.role, COUNT(ur.user_id) FROM role r LEFT JOIN user_role ur ON ur.role = r.role GROUP BY r.role",
            "SELECT 'M', user_id, role FROM user_role",
        ],
    },
    "odoo": {
        "container": "nidan-odoo-db", "shell": PSQL.format(db='"${ODOO_DB_NAME:-nidan}"'),
        "begin": "BEGIN READ ONLY;", "end": "COMMIT;",
        "queries": [
            "SELECT 'U', id, login, CASE WHEN active THEN 1 ELSE 0 END, CASE WHEN share THEN 'portal' ELSE '' END FROM res_users",
            "SELECT 'R', COALESCE(d.module || '.' || d.name, 'id:' || g.id), COUNT(r.uid) FROM res_groups g "
            "LEFT JOIN ir_model_data d ON d.model = 'res.groups' AND d.res_id = g.id "
            "LEFT JOIN res_groups_users_rel r ON r.gid = g.id GROUP BY 2",
            "SELECT 'M', r.uid, COALESCE(d.module || '.' || d.name, 'id:' || r.gid) FROM res_groups_users_rel r "
            "LEFT JOIN ir_model_data d ON d.model = 'res.groups' AND d.res_id = r.gid",
        ],
    },
    "openelis": {
        "container": "nidan-openelis-db", "shell": PSQL.format(db='"${POSTGRES_DB:-clinlims}"'),
        "begin": "BEGIN READ ONLY; SET LOCAL search_path = clinlims;", "end": "COMMIT;",
        "queries": [
            "SELECT 'U', id, login_name, CASE WHEN is_active = 'Y' THEN 1 ELSE 0 END, '' FROM system_user",
            "SELECT 'R', r.name, COUNT(ur.system_user_id) FROM system_role r "
            "LEFT JOIN system_user_role ur ON ur.role_id = r.id GROUP BY r.name",
            "SELECT 'M', ur.system_user_id, r.name FROM system_user_role ur JOIN system_role r ON r.id = ur.role_id",
        ],
    },
    "dhis2": {
        "container": "nidan-dhis2-db", "shell": MYSQL, "begin": "START TRANSACTION READ ONLY;", "end": "COMMIT;",
        "queries": [
            "SELECT 'U', id, username, IF(status = 'ACTIVE', 1, 0), '' FROM app_user",
            "SELECT 'R', role, COUNT(*) FROM app_user GROUP BY role",
            "SELECT 'M', id, role FROM app_user",
        ],
    },
    "superset": {
        "container": "nidan-superset", "sqlite": "/app/superset_home/superset.db",
        "queries": [
            "SELECT 'U', id, username, CASE WHEN active THEN 1 ELSE 0 END, '' FROM ab_user",
            "SELECT 'R', r.name, COUNT(ur.user_id) FROM ab_role r LEFT JOIN ab_user_role ur ON ur.role_id = r.id GROUP BY r.name",
            "SELECT 'M', ur.user_id, r.name FROM ab_user_role ur JOIN ab_role r ON r.id = ur.role_id",
        ],
    },
}
SQLITE_RO = ("import sqlite3,sys\n"
             "c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True)\n"
             "for q in sys.stdin.read().split(';\\n'):\n"
             "    q=q.strip()\n"
             "    if q:\n"
             "        for r in c.execute(q): print('\\t'.join('' if v is None else str(v) for v in r))\n")
WRITE_WORDS = ("insert", "update", "delete", "drop", "alter", "create", "truncate", "grant", "replace", "merge",
               "call", "lock", "into", "set ")


def script_for(app):
    """(argv, stdin) that runs the app's queries read-only inside its container."""
    a = APPS[app]
    for q in a["queries"]:
        low = " " + q.lower() + " "
        if not low.lstrip().startswith("select") or any(f" {w}" in low for w in WRITE_WORDS):
            raise ValueError(f"{app}: not a plain SELECT: {q}")
    if "sqlite" in a:
        return (["docker", "exec", "-i", a["container"], "python3", "-c", SQLITE_RO, a["sqlite"]],
                ";\n".join(a["queries"]) + ";\n")
    sql = "\n".join([a["begin"]] + [q + ";" for q in a["queries"]] + [a["end"]]) + "\n"
    return ["docker", "exec", "-i", a["container"], "sh", "-c", a["shell"]], sql


def parse(app, text):
    out = {"users": [], "roles": {}, "memberships": []}
    for line in text.splitlines():
        f = line.split("\t")
        if f[0] == "U" and len(f) >= 4:
            out["users"].append({"id": f[1], "username": f[2], "active": f[3] == "1", "note": f[4] if len(f) > 4 else ""})
        elif f[0] == "R" and len(f) >= 3:
            out["roles"][f[1]] = int(f[2] or 0)
        elif f[0] == "M" and len(f) >= 3:
            out["memberships"].append({"user_id": f[1], "role": f[2]})
    return out


def export(out_path, run=subprocess.run):
    data = {}
    for app in APPS:
        argv, sql = script_for(app)
        res = run(argv, input=sql, capture_output=True, text=True)
        if res.returncode != 0:
            data[app] = {"error": (res.stderr or "").strip()[:500]}
            print(f"{app}: FAILED ({data[app]['error'][:120]})", file=sys.stderr)
            continue
        data[app] = parse(app, res.stdout)
        print(f"{app}: {len(data[app]['users'])} users, {len(data[app]['roles'])} roles")
    Path(out_path).write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    return data


def catalog(app):
    f = CATALOG_DIR / f"{app}.yaml"
    roles = (yaml.safe_load(f.read_text()) or {}).get("roles", []) if f.exists() else []
    return {r["name"]: r for r in roles}


def inside_git(path):
    p = Path(path).resolve()
    for d in [p, *p.parents]:
        if (d / ".git").exists():
            return d
    return None


LINK_FIELDS = ["username", "app", "app_user_id", "active", "note", "same_name_in", "decision", "keycloak_sub", "comment"]


def workbook(export_path, out_dir):
    if inside_git(out_dir):
        raise SystemExit(f"refusing to write staff names inside a git work tree ({inside_git(out_dir)})")
    data = json.loads(Path(export_path).read_text(encoding="utf-8"))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    with open(out / "roles.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["app", "role", "users", "catalog", "proposed_class"])
        for app, d in sorted(data.items()):
            cat = catalog(app)
            for role, n in sorted((d.get("roles") or {}).items()):
                c = cat.get(role)
                kind = "site-only" if c is None else ("superuser" if c.get("superuser") else "base")
                w.writerow([app, role, n, kind, "unused" if n == 0 else ("site-only" if c is None else "in-use")])

    names = {}
    for app, d in data.items():
        for u in d.get("users") or []:
            names.setdefault(u["username"].lower(), set()).add(app)
    rows = {}
    link = out / "link-table.csv"
    if link.exists():
        for r in csv.DictReader(open(link, newline="", encoding="utf-8")):
            rows[(r["username"].lower(), r["app"], r["app_user_id"])] = r
    for app, d in data.items():
        for u in d.get("users") or []:
            key = (u["username"].lower(), app, str(u["id"]))
            r = rows.setdefault(key, {"decision": "", "keycloak_sub": "", "comment": ""})
            r.update(username=u["username"], app=app, app_user_id=str(u["id"]), active="yes" if u["active"] else "no",
                     note=u.get("note", ""), same_name_in=" ".join(sorted(names[key[0]] - {app})))
    with open(link, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, LINK_FIELDS, extrasaction="ignore")
        w.writeheader()
        for k in sorted(rows):
            w.writerow({f: rows[k].get(f, "") for f in LINK_FIELDS})

    by_name = {}
    for (name, _app, _id), r in rows.items():
        by_name.setdefault(name, []).append((r.get("decision") or "").strip().lower())
    reserved = sorted(n for n, ds in by_name.items() if not all(d == "link" for d in ds))
    (out / "reserved-usernames.txt").write_text("".join(n + "\n" for n in reserved), encoding="utf-8")
    print(f"workbook: {len(rows)} link rows, {len(reserved)} reserved usernames -> {out}")
    return rows, reserved


def main(argv):
    if len(argv) == 3 and argv[1] == "export":
        if inside_git(Path(argv[2]).parent):
            raise SystemExit("refusing to write staff names inside a git work tree")
        export(argv[2])
    elif len(argv) == 4 and argv[1] == "workbook":
        workbook(argv[2], argv[3])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
