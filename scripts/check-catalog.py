#!/usr/bin/env python3
"""Role catalog check (backlog KC-24 / R1, KC-57).

  scripts/check-catalog.py [MONOREPO_ROOT]           check (CI)
  scripts/check-catalog.py --write [MONOREPO_ROOT]   (re)generate keycloak/realm/roles/*.yaml from the sources
  scripts/check-catalog.py --site keycloak/realm/sites/<site> --export EXPORT.json
                                                     a site overlay's roles must exist in that site's export (KC-24-03)

Catalogs (keycloak/realm/roles/<app>.yaml) list every role/group Keycloak may hand to an app:
  - {name, source, superuser}; source = the file that defines it in this monorepo, or "core" for
    roles that ship with the app/platform (verified at a site by the bootstrap exporter, KC-25).
Fails when:
  1. a catalog entry with an in-repo source no longer exists there (renamed/removed role, R1), or a
     role defined in the sources is missing from the catalog;
  2. a role the SOURCE says is superuser is not flagged superuser in the catalog;
  3. any staff/* job group in the realm maps a superuser role (KC-57: those live only in the
     operator-managed app-superusers group).
Superuser is derived, not trusted: OpenMRS roles whose privileges (incl. inherited) include an
administrative privilege; Odoo groups implying base.group_system / base.group_erp_manager.
"""
import csv
import glob
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
CATALOG_DIR = HERE.parent / "keycloak" / "realm" / "roles"
REALM_DIR = HERE.parent / "keycloak" / "realm"

OPENMRS_ADMIN_PRIVS = {"Manage Roles", "Manage Modules", "Manage Privileges", "Edit Users", "Edit User Passwords",
                       "Assign System Developer Role", "Manage Global Properties", "Manage Scheduler"}
ODOO_SUPER = {"base.group_system", "base.group_erp_manager"}
CORE = {
    "openmrs": [("System Developer", True), ("Provider", False), ("Authenticated", False)],
    "odoo": [("base.group_user", False), ("base.group_system", True), ("base.group_erp_manager", True),
             ("base.group_no_one", True), ("point_of_sale.group_pos_user", False)],
    "openelis": [("Global Administrator", True), ("User Account Administrator", True), ("Reception", False),
                 ("Results", False), ("Validation", False), ("Reports", False)],
    "superset": [("Admin", True), ("Alpha", True), ("sql_lab", True), ("Gamma", False), ("Public", False)],
    # dhis2 reporter app (nidan-dhis2-integration auth/oidc.py ROLE_MAP). report-admin writes SQL reports over the
    # clinical databases, so it is treated as superuser (never in a staff job, KC-57).
    "dhis2": [("admin", True), ("report-admin", True), ("report-user", False), ("reporter", False)],
}


def openmrs_inheritance(root):
    """{role: [inherited roles]} from the content CSVs."""
    return {n: v["inherits"] for n, v in openmrs_rows(root).items()}


def openmrs_roles(root):
    rows = openmrs_rows(root)

    def all_privs(name, seen=()):
        if name not in rows or name in seen:
            return set()
        s = set(rows[name]["privs"])
        for parent in rows[name]["inherits"]:
            s |= all_privs(parent, seen + (name,))
        return s

    return {n: (v["source"], bool(all_privs(n) & OPENMRS_ADMIN_PRIVS) or "System Developer" in v["inherits"])
            for n, v in rows.items()}


def openmrs_rows(root):
    rows = {}
    for f in sorted(glob.glob(str(root / "openmrs-backend/openmrs-content-referenceapplication-demo/configuration/roles/**/*.csv"), recursive=True)):
        for r in csv.DictReader(open(f, newline="", encoding="utf-8")):
            name = (r.get("Role name") or "").strip()
            if name:
                rows[name] = {
                    "source": str(Path(f).relative_to(root)),
                    "privs": {p.strip() for p in (r.get("Privileges") or "").split(";") if p.strip()},
                    "inherits": [p.strip() for p in (r.get("Inherited roles") or "").split(";") if p.strip()],
                }
    return rows


def odoo_groups(root):
    found, implied = odoo_xml(root)

    def is_super(x, seen=()):
        if x in ODOO_SUPER:
            return True
        return any(is_super(i, seen + (x,)) for i in implied.get(x, ()) if i not in seen)

    return {x: (src, is_super(x)) for x, src in found.items()}


def odoo_xml(root):
    """({xml id: source file}, {xml id: {implied xml ids}}) for every res.groups record in the addons."""
    found, implied = {}, {}
    for repo in ("odoo_19_addons", "nidan_odoo_extra_addons", "odoo_payment_gateway"):
        for f in glob.glob(str(root / repo / "*" / "**" / "*.xml"), recursive=True):
            mod = Path(f).relative_to(root / repo).parts[0]
            text = open(f, encoding="utf-8", errors="ignore").read()
            for rec in re.finditer(r'<record\b([^>]*)>(.*?)</record>', text, re.S):
                attrs, body = rec.group(1), rec.group(2)
                if 'model="res.groups"' not in attrs:
                    continue
                m = re.search(r'\bid="([^"]+)"', attrs)
                if not m:
                    continue
                xid = m.group(1) if "." in m.group(1) else f"{mod}.{m.group(1)}"
                found.setdefault(xid, str(Path(f).relative_to(root)))
                full = lambda r: r if "." in r else f"{mod}.{r}"
                for field, ev in re.findall(r'name="(implied_ids|implied_by_ids)"[^>]*eval="([^"]*)"', body):
                    for r in re.findall(r"ref\(\s*'([^']+)'\s*\)", ev):
                        if field == "implied_ids":      # xid implies r
                            implied.setdefault(xid, set()).add(full(r))
                        else:                           # r implies xid
                            implied.setdefault(full(r), set()).add(xid)
    return found, implied


def openelis_roles(root):
    out = {}
    for f in glob.glob(str(root / "OpenELIS-Global-2/src/main/resources/liquibase/**/*.xml"), recursive=True):
        text = open(f, encoding="utf-8", errors="ignore").read()
        for blk in re.findall(r'<insert[^>]*tableName="system_role"[^>]*>(.*?)</insert>', text, re.S):
            m = re.search(r'<column\s+name="name"\s+value="([^"]+)"', blk)  # any spacing between attributes
            if m:
                out.setdefault(m.group(1), (str(Path(f).relative_to(root)), False))
    return out


def superset_roles(root):
    f = root / "nidan-docker/superset/init_superset_metadata.py"
    m = re.search(r"STAFF_ROLES\s*=\s*\[([^\]]*)\]", f.read_text(encoding="utf-8")) if f.exists() else None
    return {n: (str(f.relative_to(root)), False) for n in re.findall(r'"([^"]+)"', m.group(1))} if m else {}


SOURCES = {"openmrs": openmrs_roles, "odoo": odoo_groups, "openelis": openelis_roles, "superset": superset_roles,
           "dhis2": lambda root: {}}


def derived(root):
    out = {}
    for app, fn in SOURCES.items():
        roles = {n: {"name": n, "source": src, "superuser": sup} for n, (src, sup) in fn(root).items()}
        for n, sup in CORE[app]:
            roles.setdefault(n, {"name": n, "source": "core", "superuser": sup})
        out[app] = roles
    return out


def write(root):
    CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    for app, roles in derived(root).items():
        body = sorted(roles.values(), key=lambda r: (r["source"] == "core", r["name"]))
        (CATALOG_DIR / f"{app}.yaml").write_text(
            f"# {app} roles Keycloak may grant (backlog KC-24, R1). Generated by scripts/check-catalog.py --write,\n"
            "# then reviewed. superuser roles may only be mapped by the operator-managed app-superusers group (KC-57).\n"
            + yaml.safe_dump({"app": app, "roles": body}, sort_keys=False, allow_unicode=True), encoding="utf-8")
        print(f"wrote {app}.yaml ({len(body)} roles)")


def realm_docs():
    # role catalogs (roles/<app>.yaml) and site role overlays (sites/<site>/roles/) are not realm documents
    return [yaml.safe_load(f.read_text()) or {} for f in sorted(REALM_DIR.rglob("*.yaml"))
            if CATALOG_DIR not in f.parents and f.parent.name != "roles"]


def composites(docs):
    """(client|None, role) -> {(client|None, role)} for every composite role in the realm files."""
    out = {}
    for doc in docs:
        roles = doc.get("roles") or {}
        defs = [(None, r) for r in roles.get("realm") or []]
        defs += [(c, r) for c, rs in (roles.get("client") or {}).items() for r in rs or []]
        for owner, r in defs:
            comp = r.get("composites") or {}
            kids = {(None, n) for n in comp.get("realm") or []}
            kids |= {(c, n) for c, ns in (comp.get("client") or {}).items() for n in ns or []}
            out.setdefault((owner, r.get("name")), set()).update(kids)
    return out


def job_role_mappings():
    """(job group path, client, role) for every staff/* job in the realm files and job overlays, with
    Keycloak composite roles expanded (KC-57-01 "directly or via composite")."""
    docs = realm_docs()
    comp = composites(docs)

    def expand(key, seen):
        if key in seen:
            return set()
        seen.add(key)
        s = {key}
        for kid in comp.get(key, ()):
            s |= expand(kid, seen)
        return s

    out = []
    for doc in docs:
        def walk(groups, prefix):
            for g in groups or []:
                path = f"{prefix}/{g.get('name')}"
                if path.startswith("/staff/"):
                    direct = {(None, r) for r in g.get("realmRoles") or []}
                    direct |= {(c, r) for c, rs in (g.get("clientRoles") or {}).items() for r in rs or []}
                    for key in direct:
                        out.extend((path, c, r) for c, r in expand(key, set()) if c)
                walk(g.get("subGroups"), path)
        walk(doc.get("groups"), "")
    return out


# R3: the role every job that opens the app must carry. OpenMRS adds "Authenticated" to every user
# itself; OpenELIS has no login-only role (a lab job maps its own working roles).
BASELINE = {"odoo": "base.group_user", "superset": "Gamma", "dhis2": "reporter"}
GATEWAY_APP = {"app-openmrs": "openmrs", "app-odoo": "odoo", "app-openelis": "openelis", "app-superset": "superset",
               "app-dhis2": "dhis2"}
ADMIN_GROUPS = {"/app-superusers", "/hospital-admin", "/cis-admin", "/orthanc-admin"}
NEVER_STAFF = {"admin", "nidan", "nidan-sync", "daemon", "__system__", "dead-letter-admin"}


def job_and_user_rules(mappings, docs):
    """KC-08-09 (R3 baseline, R6 no admin/service account in a job) and KC-09-08 (static: nobody in
    both an admin group and a staff job)."""
    problems = []
    jobs = {}
    for path, client, role in mappings:
        jobs.setdefault(path, set()).add((client, role))
    for doc in docs:
        def walk(groups, prefix):
            for g in groups or []:
                path = f"{prefix}/{g.get('name')}"
                if path.startswith("/staff/"):
                    jobs.setdefault(path, set())
                walk(g.get("subGroups"), path)
        walk(doc.get("groups"), "")
    for path, roles in sorted(jobs.items()):
        opens = {GATEWAY_APP[r] for c, r in roles if c == "nidan-gateway" and r in GATEWAY_APP}
        opens |= {c for c, _ in roles if c in BASELINE}
        for app in sorted(opens & set(BASELINE)):
            if (app, BASELINE[app]) not in roles:
                problems.append(f"{path}: opens {app} but lacks its baseline role {BASELINE[app]!r} (R3)")
    for doc in docs:
        for u in doc.get("users") or []:
            name = str(u.get("username", "")).lower()
            groups = {"/" + g.lstrip("/") for g in u.get("groups") or []}
            staff = sorted(g for g in groups if g.startswith("/staff/"))
            if staff and (name in NEVER_STAFF or name.startswith("service-account-")):
                problems.append(f"user {name}: admin/service account in job {staff[0]} (R6)")
            if staff and groups & ADMIN_GROUPS:
                problems.append(f"user {name}: in {sorted(groups & ADMIN_GROUPS)[0]} and job {staff[0]} (KC-09-08)")
    return problems


def check(root):
    problems = job_and_user_rules(job_role_mappings(), realm_docs())
    truth = derived(root)
    for app, roles in truth.items():
        f = CATALOG_DIR / f"{app}.yaml"
        if not f.exists():
            problems.append(f"{app}: catalog {f.name} missing (run --write, then review)")
            continue
        cat = {r["name"]: r for r in (yaml.safe_load(f.read_text()) or {}).get("roles", [])}
        for name, r in cat.items():
            if r.get("source") != "core" and name not in roles:
                problems.append(f"{app}: catalog role {name!r} no longer defined in {r.get('source')} (renamed/removed?)")
        for name, r in roles.items():
            if r["source"] != "core" and name not in cat:
                problems.append(f"{app}: {name!r} defined in {r['source']} is missing from the catalog")
            elif name in cat and r["superuser"] and not cat[name].get("superuser"):
                problems.append(f"{app}: {name!r} is superuser per its source but not flagged in the catalog")
    supers = {(app, n) for app in truth for n, r in truth[app].items() if r["superuser"]}
    for app in truth:
        f = CATALOG_DIR / f"{app}.yaml"
        if f.exists():
            supers |= {(app, r["name"]) for r in (yaml.safe_load(f.read_text()) or {}).get("roles", []) if r.get("superuser")}
    for f in sorted((REALM_DIR / "sites").glob("*/roles/*.yaml")):  # site-only roles flagged superuser
        doc = yaml.safe_load(f.read_text()) or {}
        supers |= {(doc.get("app", f.stem), r["name"]) for r in doc.get("roles", []) if r.get("superuser")}
    for path, client, role in job_role_mappings():
        if (client, role) in supers:
            problems.append(f"{path}: maps superuser role {client}:{role!r}; only app-superusers may (KC-57)")
    return problems


def site_roles(site_dir):
    """{app: {role names}} from <site>/roles/<app>.yaml (site-only roles: no source in this monorepo)."""
    out = {}
    for f in sorted((Path(site_dir) / "roles").glob("*.yaml")):
        doc = yaml.safe_load(f.read_text()) or {}
        out[doc.get("app", f.stem)] = {r["name"] for r in doc.get("roles", [])}
    return out


def check_site(site_dir, export):
    """KC-24-03: every overlay role is present in the site's bootstrap export, and none shadows a base role."""
    problems = []
    base = derived(Path(__file__).resolve().parent.parent.parent)
    for app, names in site_roles(site_dir).items():
        d = export.get(app) or {}
        if "error" in d or not d:
            problems.append(f"{app}: no export data for the site, cannot confirm {len(names)} overlay role(s)")
            continue
        for n in sorted(names):
            if n not in (d.get("roles") or {}):
                problems.append(f"{app}: overlay role {n!r} is not in the site export")
            if n in base.get(app, {}):
                problems.append(f"{app}: overlay role {n!r} is already a base catalog role")
    return problems


def main():
    if "--site" in sys.argv:
        import json
        a = sys.argv
        found = check_site(a[a.index("--site") + 1], json.loads(Path(a[a.index("--export") + 1]).read_text()))
        print(f"site overlay: {len(found)} problem(s)")
        for p in found:
            print("  " + p)
        return 1 if found else 0
    args = [a for a in sys.argv[1:] if a != "--write"]
    root = Path(args[0]).resolve() if args else HERE.parent.parent
    if "--write" in sys.argv:
        write(root)
        return 0
    problems = check(root)
    print(f"role catalogs: {len(problems)} problem(s)")
    for p in problems:
        print("  " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
