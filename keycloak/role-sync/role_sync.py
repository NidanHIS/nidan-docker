#!/usr/bin/env python3
"""Keycloak role sync: each app's client roles come from the app itself, not from files in this repository.

  python3 -I role_sync.py             sync now, then every ROLE_SYNC_INTERVAL seconds (default 3600)
  python3 -I role_sync.py --once      sync once and exit (on demand:
                                      docker compose exec keycloak-role-sync python3 -I /app/role_sync.py --once)
  python3 -I role_sync.py --test      offline self-check

Sources (read only; each app answers with the roles it really has, with the modules it really has installed):
  openmrs   GET /openmrs/ws/rest/v1/nidan/role-catalog with a role-sync client-credentials token (nidancore;
            account nidan-rolesync, privileges Get Roles + Get Privileges only)
  odoo      GET /api/nidan/role-catalog         header X-Nidan-Role-Sync-Secret (nidan_connector)
  openelis  GET /OpenELIS-Global/rest/nidan/role-catalog, same header
Superset and DHIS2 roles are fixed in their configuration and declared in keycloak/realm/base/nidan.yaml.

In Keycloak, per app client:
  - an app role Keycloak lacks is created, marked nidan_source=<app>-sync (and nidan_superuser=true for superuser
    roles, KC-57: those belong only to the operator-managed app-superusers group);
  - a role this job created that the app no longer has is deleted only when nothing uses it (no user, no group,
    no composite role); a role still in use is kept and reported. Roles from the realm files are never touched;
  - nothing is deleted when an app answers with no roles or cannot be read;
  - a staff job group holding a superuser role is reported as an error (exit 1 with --once).
Keycloak admin access: master-realm client nidan-role-sync (manage-clients, view-clients, view-users, view-realm
on realm nidan; view-realm only to see which composite roles include a role before deleting it).
Never logs secrets or tokens.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

KC = os.environ.get("KEYCLOAK_URL", "http://keycloak:8080/auth").rstrip("/")
REALM = os.environ.get("KEYCLOAK_REALM", "nidan")
SOURCE = "nidan_source"
SUPER = "nidan_superuser"
MISSING = "nidan_missing_since"      # epoch seconds: first run that did not see the role in the app
# a role gone from the app is deleted only after this long (a partial answer, e.g. during an upgrade, deletes nothing)
DELETE_AFTER = int(os.environ.get("ROLE_SYNC_DELETE_AFTER", "86400"))

# OpenMRS: superuser = an administrative privilege, directly or inherited (also used by scripts/bootstrap-export.py);
# the machine accounts' roles never go to people
OPENMRS_ADMIN_PRIVS = {"Manage Roles", "Manage Modules", "Manage Privileges", "Edit Users", "Edit User Passwords",
                       "Assign System Developer Role", "Manage Global Properties", "Manage Scheduler"}
OPENMRS_SKIP = {"Anonymous", "Nidan Sync", "Nidan Odoo Read", "Nidan Portal", "Nidan Role Sync"}


def log(msg):
    print(f"role-sync: {msg}", flush=True)


def http(method, url, token=None, body=None, headers=None, form=None):
    h = dict(headers or {})
    data = None
    if token:
        h["Authorization"] = f"Bearer {token}"
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def client_token(realm, client_id, secret):
    form = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret}
    return http("POST", f"{KC}/realms/{realm}/protocol/openid-connect/token", form=form)["access_token"]


# ---- sources: {role name: {"description": str, "superuser": bool}} ----

def openmrs_roles(rows):
    """rows: [{name, description, privileges: [{name}], inheritedRoles: [{name}]}] (nidancore RoleCatalogController)."""
    by = {r["name"]: r for r in rows}

    def privs(name, seen=()):
        r = by.get(name)
        if not r or name in seen:
            return set(), False
        p = {x["name"] for x in r.get("privileges") or []}
        dev = name == "System Developer"
        for parent in r.get("inheritedRoles") or []:
            pn = parent.get("name") or parent.get("display")
            pp, pd = privs(pn, seen + (name,))
            p |= pp
            dev = dev or pd
        return p, dev

    out = {}
    for name, r in by.items():
        if name in OPENMRS_SKIP or name != name.strip():
            continue
        p, dev = privs(name)
        out[name] = {"description": r.get("description") or "", "superuser": dev or bool(p & OPENMRS_ADMIN_PRIVS)}
    return out


def fetch_openmrs():
    url = os.environ.get("OPENMRS_URL", "").rstrip("/")
    secret = os.environ.get("KC_ROLE_SYNC_CLIENT_SECRET", "")
    if not url or not secret:
        return None
    token = client_token(REALM, "role-sync", secret)
    # nidancore's read-only list (the REST role resource asks for Manage Roles even to read)
    rows = http("GET", f"{url}/ws/rest/v1/nidan/role-catalog", token)["results"]
    return openmrs_roles(rows)


def fetch_catalog(url_env, path):
    url = os.environ.get(url_env, "").rstrip("/")
    secret = os.environ.get("NIDAN_ROLE_SYNC_SECRET", "")
    if not url or len(secret) < 32:
        return None
    body = http("GET", url + path, headers={"X-Nidan-Role-Sync-Secret": secret})
    return {r["name"].strip(): {"description": (r.get("description") or "").strip(),
                                "superuser": bool(r.get("superuser"))}
            for r in body["roles"] if r["name"].strip()}


SOURCES = {
    "openmrs": fetch_openmrs,
    "odoo": lambda: fetch_catalog("ODOO_URL", "/api/nidan/role-catalog"),
    "openelis": lambda: fetch_catalog("OPENELIS_URL", "/OpenELIS-Global/rest/nidan/role-catalog"),
}


# ---- Keycloak ----

class Keycloak:
    def __init__(self):
        self.base = f"{KC}/admin/realms/{REALM}"
        self.refresh()

    def refresh(self):
        """A fresh admin token (master tokens live about a minute): once per app."""
        self.token = client_token("master", "nidan-role-sync", os.environ["KC_ROLE_SYNC_ADMIN_SECRET"])

    def get(self, path):
        return http("GET", self.base + path, self.token)

    def client_id(self, client):
        found = self.get(f"/clients?clientId={urllib.parse.quote(client)}")
        return found[0]["id"] if found else None

    def roles(self, cid):
        return {r["name"]: r for r in self.get(f"/clients/{cid}/roles?briefRepresentation=false&max=10000")}

    def role_path(self, cid, name):
        return f"/clients/{cid}/roles/{urllib.parse.quote(name, safe='')}"

    def in_use(self, cid, name, composite_children):
        p = self.role_path(cid, name)
        return bool(self.get(p + "/users?first=0&max=1") or self.get(p + "/groups?first=0&max=1")
                    or name in composite_children)

    def composite_children(self, cid):
        """Names of this client's roles that some composite role (realm or any client) includes."""
        kids = set()
        parents = [r for r in self.get("/roles?briefRepresentation=false&max=10000") if r.get("composite")]
        for c in self.get("/clients?max=1000"):
            parents += [r for r in self.get(f"/clients/{c['id']}/roles?briefRepresentation=false&max=10000")
                        if r.get("composite")]
        for r in parents:
            for k in self.get(f"/roles-by-id/{r['id']}/composites/clients/{cid}"):
                kids.add(k["name"])
        return kids

    def staff_groups(self):
        """[(path, id)] of every group below /staff (children read one level at a time: a search filters them)."""
        out = [(g["path"], g["id"]) for g in self.get("/groups?max=1000") if g["path"] == "/staff"]
        todo = [i for _, i in out]           # /staff itself too: its roles reach every job below it
        while todo:
            for g in self.get(f"/groups/{todo.pop()}/children?max=1000"):
                out.append((g["path"], g["id"]))
                todo.append(g["id"])
        return out


def valid_name(name):
    """Keycloak keeps role names up to 255 characters; a "/" does not survive its admin URL paths."""
    return 0 < len(name) <= 255 and "/" not in name and not any(ord(c) < 32 for c in name)


def plan(app, live, kc_roles, now=None):
    """(create, update, delete_candidates) for one app. Pure: tested offline. A role this job made that the app no
    longer has is first marked missing (an update); it becomes a delete candidate DELETE_AFTER seconds later."""
    now = int(time.time()) if now is None else now
    mark = f"{app}-sync"
    create, update, gone = [], [], []
    for name, info in sorted(live.items()):
        want = {SOURCE: [mark], SUPER: ["true"]} if info["superuser"] else {SOURCE: [mark]}
        have = kc_roles.get(name)
        if have is None:
            create.append({"name": name, "description": info["description"][:255], "attributes": want})
        elif (have.get("attributes") or {}).get(SOURCE) == [mark] and (
                (have.get("attributes") or {}) != want or (have.get("description") or "") != info["description"][:255]):
            update.append({**have, "description": info["description"][:255], "attributes": want})
    if live:                            # an empty answer never deletes everything
        for n, r in sorted(kc_roles.items()):
            attrs = r.get("attributes") or {}
            if n in live or attrs.get(SOURCE) != [mark]:
                continue
            since = (attrs.get(MISSING) or [""])[0]
            if not since.isdigit():
                update.append({**r, "attributes": {**attrs, MISSING: [str(now)]}})
            elif now - int(since) >= DELETE_AFTER:
                gone.append(n)
    return create, update, gone


def sync_app(kc, app, live):
    """One app: create / update / delete-when-unused, then the KC-57 check. Returns the number of problems."""
    kc.refresh()
    cid = kc.client_id(app)
    if not cid:
        log(f"{app}: no Keycloak client {app!r}; skipped")
        return 0
    problems = 0
    bad = sorted(n for n in live if not valid_name(n))
    if bad:
        log(f"{app}: skipped {len(bad)} role name(s) Keycloak cannot hold (over 255 characters, '/' or control "
            f"characters): {[n[:60] for n in bad]}")
        live = {n: i for n, i in live.items() if n not in bad}
    try:
        create, update, gone = plan(app, live, kc.roles(cid))
        failed = []
        for method, rows in (("POST", create), ("PUT", update)):
            for r in rows:                  # one refused role never stops the others
                try:
                    url = f"{kc.base}/clients/{cid}/roles" if method == "POST" else kc.base + kc.role_path(cid, r["name"])
                    http(method, url, kc.token, r)
                except (OSError, ValueError) as e:
                    failed.append(f"{r['name'][:60]} ({getattr(e, 'code', '') or type(e).__name__})")
        deleted, kept = [], []
        children = kc.composite_children(cid) if gone else set()
        for name in gone:
            try:
                if kc.in_use(cid, name, children):      # checked right before the delete
                    kept.append(name)
                else:
                    http("DELETE", kc.base + kc.role_path(cid, name), kc.token)
                    deleted.append(name)
            except (OSError, ValueError) as e:
                failed.append(f"{name[:60]} ({getattr(e, 'code', '') or type(e).__name__})")
        marked = sum(1 for r in update if MISSING in (r.get("attributes") or {}))
        log(f"{app}: {len(live)} roles in the app; created {len(create)}, updated {len(update) - marked}, "
            f"marked missing {marked}, deleted {deleted or 0}"
            + (f"; KEPT (gone from the app but still assigned, remove the assignments first): {kept}" if kept else "")
            + (f"; FAILED: {failed}" if failed else ""))
        problems += 1 if failed else 0
    finally:
        # KC-57 at runtime, whatever happened above: a staff job never carries a superuser role (directly, through a
        # composite, or from /staff itself)
        superusers = {n for n, i in live.items() if i["superuser"]}
        for path, gid in kc.staff_groups():
            held = {r["name"] for r in kc.get(f"/groups/{gid}/role-mappings/clients/{cid}/composite")}
            for name in sorted(held & superusers):
                log(f"ERROR {path} holds the {app} superuser role {name!r} (KC-57): move it to app-superusers")
                problems += 1
    return problems


def sync_once():
    kc = Keycloak()
    problems = 0
    for app, fetch in SOURCES.items():
        try:
            live = fetch()
        except (OSError, KeyError, ValueError) as e:      # urllib errors are OSErrors
            log(f"{app}: could not read its roles ({type(e).__name__}: {getattr(e, 'code', '') or e}); left as it is")
            problems += 1
            continue
        if live is None:
            continue                    # not configured on this site
        try:
            problems += sync_app(kc, app, live)
        except (OSError, KeyError, ValueError) as e:      # one app's failure never stops the others
            log(f"{app}: Keycloak update failed ({type(e).__name__}: {getattr(e, 'code', '') or e})")
            problems += 1
    return problems


def self_test():
    rows = [
        {"name": "Authenticated", "privileges": [], "inheritedRoles": []},
        {"name": "Nurse", "description": "ward", "privileges": [{"name": "Get Patients"}], "inheritedRoles": []},
        {"name": "Head Nurse", "privileges": [], "inheritedRoles": [{"name": "Nurse"}, {"display": "Sysadmin"}]},
        {"name": "Sysadmin", "privileges": [{"name": "Manage Roles"}], "inheritedRoles": []},
        {"name": "Dev", "privileges": [], "inheritedRoles": [{"name": "System Developer"}]},
        {"name": "System Developer", "privileges": [], "inheritedRoles": []},
        {"name": "Loop", "privileges": [], "inheritedRoles": [{"name": "Loop"}]},
        {"name": "Nidan Sync", "privileges": [{"name": "Edit Users"}], "inheritedRoles": []},
    ]
    o = openmrs_roles(rows)
    assert not o["Nurse"]["superuser"] and o["Nurse"]["description"] == "ward"
    assert o["Head Nurse"]["superuser"] and o["Sysadmin"]["superuser"] and o["Dev"]["superuser"]
    assert o["System Developer"]["superuser"] and not o["Loop"]["superuser"] and "Nidan Sync" not in o

    live = {"Nurse": {"description": "ward", "superuser": False}, "Admin": {"description": "", "superuser": True}}
    kc = {"Nurse": {"name": "Nurse", "description": "old", "attributes": {SOURCE: ["openmrs-sync"]}},
          "Old": {"name": "Old", "attributes": {SOURCE: ["openmrs-sync"]}},
          "FromFile": {"name": "FromFile", "attributes": {}},
          "OtherApp": {"name": "OtherApp", "attributes": {SOURCE: ["odoo-sync"]}}}
    c, u, g = plan("openmrs", live, kc, now=1000)
    assert [r["name"] for r in c] == ["Admin"] and c[0]["attributes"][SUPER] == ["true"]
    assert [r["name"] for r in u] == ["Nurse", "Old"] and u[0]["description"] == "ward"
    assert u[1]["attributes"][MISSING] == ["1000"] and g == [], (u, g)   # first marked, never deleted at once
    kc["Old"]["attributes"][MISSING] = ["1000"]
    assert plan("openmrs", live, kc, now=1000 + DELETE_AFTER - 1)[2] == []
    assert plan("openmrs", live, kc, now=1000 + DELETE_AFTER)[2] == ["Old"]   # never the realm file's or another app's
    back = plan("openmrs", {**live, "Old": {"description": "", "superuser": False}}, kc, now=5000)[1]
    assert any(r["name"] == "Old" and MISSING not in r["attributes"] for r in back)   # back in the app: unmarked
    assert valid_name("Nurse") and not valid_name("x/y") and not valid_name("a" * 256) and not valid_name("a\nb")
    assert plan("openmrs", {}, kc, now=10 ** 10) == ([], [], [])   # an empty answer deletes nothing
    same = {"Nurse": {"name": "Nurse", "description": "ward", "attributes": {SOURCE: ["openmrs-sync"]}}}
    assert plan("openmrs", {"Nurse": live["Nurse"]}, same) == ([], [], [])
    print("role_sync self-test ok")


def main(argv):
    if "--test" in argv:
        self_test()
        return 0
    if "--once" in argv:
        return 1 if sync_once() else 0
    interval = int(os.environ.get("ROLE_SYNC_INTERVAL", "3600"))
    while True:
        try:
            sync_once()
        except Exception as e:          # keep the loop alive; the next run tries again
            log(f"run failed ({type(e).__name__}: {getattr(e, 'code', '') or e})")
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
