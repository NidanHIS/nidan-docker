#!/usr/bin/env python3
"""Hospital-admin scope inside Keycloak (backlog KC-09): fine-grained admin permissions v2 for the Staff page.

  KC_TOKEN_FILE=/secure/token scripts/keycloak-admin-permissions.py [--apply] [--url http://127.0.0.1:8180/auth]

Keycloak stores these permissions by internal ids (groups, resources, scopes), which differ per site, so they
cannot live in the realm YAML; the realm file only turns the feature on (adminPermissionsEnabled). This script
resolves the groups by path and makes realm "nidan" hold exactly:
  policy      is-hospital-admin            member of /hospital-admin
  permission  hospital-admin-staff-groups  Groups = every /staff/<job> group: view, view-members, manage-members,
                                           manage-membership, manage-membership-of-members
  policy      not-hospital-admin           NEGATIVE of the above: denies hospital admins
  permission  admin-groups-out-of-reach    Groups = /hospital-admin, /app-superusers, /cis-admin, /orthanc-admin:
                                           a member of these is never managed through a job they also hold
  policy      is-role-sync                 the client role-sync-admin (keycloak/role-sync's admin access)
  permission  role-sync-app-clients        Clients = openmrs, odoo, openelis: manage (create / delete their roles)
Nothing is granted on any other user or group, so admins, app-superusers members and service accounts are out of
reach (403), and so are role mapping and impersonation (KC-09-04/05/06). Credential deletion is part of Keycloak's
"manage" and cannot be split off here: the gateway's /staff-api allow-list refuses it (KC-09-07).
Idempotent; dry run (prints the plan) unless --apply. Re-run after adding a job group.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

POLICY = "is-hospital-admin"
PERMISSION = "hospital-admin-staff-groups"
SCOPES = ["view", "view-members", "manage-members", "manage-membership", "manage-membership-of-members"]
DENY_POLICY = "not-hospital-admin"
DENY_PERMISSION = "admin-groups-out-of-reach"
ADMIN_GROUPS = ["/hospital-admin", "/app-superusers", "/cis-admin", "/orthanc-admin"]
DENY_SCOPES = ["view-members", "manage-members", "manage-membership", "manage-membership-of-members"]
RS_POLICY = "is-role-sync"
RS_PERMISSION = "role-sync-app-clients"
RS_CLIENT = "role-sync-admin"
RS_APPS = ["openmrs", "odoo", "openelis"]        # the apps whose roles keycloak/role-sync reads


class Api:
    def __init__(self, base, token, opener=urllib.request.urlopen):
        self.base, self.token, self.opener = base.rstrip("/") + "/admin/realms/nidan", token, opener

    def __call__(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        with self.opener(req, timeout=15) as resp:
            text = resp.read().decode()
            return json.loads(text) if text else None


def plan(api):
    """[(description, method, path, body)] to reach the desired state; [] when already there."""
    realm = api("GET", "")
    if not realm.get("adminPermissionsEnabled"):
        raise SystemExit("realm nidan has adminPermissionsEnabled=false: import keycloak/realm first")
    clients = api("GET", "/clients?clientId=admin-permissions")
    if not clients:
        raise SystemExit("no admin-permissions client in realm nidan")
    authz = f"/clients/{clients[0]['id']}/authz/resource-server"
    groups = {g["path"]: g for g in api("GET", "/groups?briefRepresentation=true")}
    if "/hospital-admin" not in groups or "/staff" not in groups:
        raise SystemExit("groups /hospital-admin and /staff must exist")
    jobs = sorted((g for g in api("GET", f"/groups/{groups['/staff']['id']}/children?max=200")
                   if g.get("path") == f"/staff/{g.get('name')}"), key=lambda g: g["name"])
    if not jobs:
        raise SystemExit("no /staff/<job> groups")
    steps = []
    policies = {p["name"]: p for p in api("GET", f"{authz}/policy?max=500")}
    want_groups = [{"id": groups["/hospital-admin"]["id"], "extendChildren": False}]
    pol = policies.get(POLICY)
    policy_body = {"name": POLICY, "type": "group", "logic": "POSITIVE", "decisionStrategy": "UNANIMOUS",
                   "description": "member of /hospital-admin (KC-09)", "groups": want_groups}
    if not pol:
        steps.append((f"create policy {POLICY}", "POST", f"{authz}/policy/group", policy_body))
        policy_ref = POLICY
    else:
        detail = api("GET", f"{authz}/policy/group/{pol['id']}")   # the list leaves config out
        have = detail.get("groups") or json.loads((detail.get("config") or {}).get("groups") or "[]")
        if sorted(g["id"] for g in have) != [g["id"] for g in want_groups] or detail.get("logic") != "POSITIVE":
            steps.append((f"update policy {POLICY}", "PUT", f"{authz}/policy/group/{pol['id']}", dict(policy_body, id=pol["id"])))
        policy_ref = pol["id"]
    perm = policies.get(PERMISSION)
    perm_body = {"name": PERMISSION, "type": "scope", "resourceType": "Groups", "decisionStrategy": "UNANIMOUS",
                 "description": "Staff page: /staff/* job groups only (KC-09)",
                 "resources": [g["id"] for g in jobs], "scopes": SCOPES, "policies": [policy_ref]}
    if not perm:
        steps.append((f"create permission {PERMISSION} on {', '.join(g['name'] for g in jobs)}", "POST",
                      f"{authz}/permission/scope", perm_body))
    else:
        detail = api("GET", f"{authz}/permission/scope/{perm['id']}")
        # Groups-type resources are named by the group id (_id is the resource's own id)
        have_res = sorted(r["name"] for r in api("GET", f"{authz}/policy/{perm['id']}/resources") or [])
        have_scopes = sorted(s["name"] for s in api("GET", f"{authz}/policy/{perm['id']}/scopes") or [])
        have_pols = sorted(p["id"] for p in api("GET", f"{authz}/policy/{perm['id']}/associatedPolicies") or [])
        if (have_res != sorted(perm_body["resources"]) or have_scopes != sorted(SCOPES)
                or (pol and have_pols != [pol["id"]]) or detail.get("decisionStrategy") != "UNANIMOUS"):
            steps.append((f"update permission {PERMISSION} to {', '.join(g['name'] for g in jobs)}", "PUT",
                          f"{authz}/permission/scope/{perm['id']}", dict(perm_body, id=perm["id"])))
    return steps + deny_plan(api, authz, policies, groups) + role_sync_plan(api, authz, policies)


def deny_plan(api, authz, policies, groups):
    """Review 2026-10-13: someone in an admin group who also holds a job (KC-09-08 forbids it, an operator can still do
    it) was managed through the job's permission: a hospital admin could reset their password. A NEGATIVE policy on
    the admin groups takes them out of reach whatever job they hold."""
    steps = []
    want_groups = [{"id": groups["/hospital-admin"]["id"], "extendChildren": False}]
    pol = policies.get(DENY_POLICY)
    body = {"name": DENY_POLICY, "type": "group", "logic": "NEGATIVE", "decisionStrategy": "UNANIMOUS",
            "description": "denies members of /hospital-admin (review 2026-10-13)", "groups": want_groups}
    if not pol:
        steps.append((f"create policy {DENY_POLICY}", "POST", f"{authz}/policy/group", body))
        ref = DENY_POLICY
    else:
        detail = api("GET", f"{authz}/policy/group/{pol['id']}")
        have = detail.get("groups") or json.loads((detail.get("config") or {}).get("groups") or "[]")
        if sorted(g["id"] for g in have) != [g["id"] for g in want_groups] or detail.get("logic") != "NEGATIVE":
            steps.append((f"update policy {DENY_POLICY}", "PUT", f"{authz}/policy/group/{pol['id']}", dict(body, id=pol["id"])))
        ref = pol["id"]
    targets = sorted(groups[p]["id"] for p in ADMIN_GROUPS if p in groups)
    perm = policies.get(DENY_PERMISSION)
    pbody = {"name": DENY_PERMISSION, "type": "scope", "resourceType": "Groups", "decisionStrategy": "UNANIMOUS",
             "description": "admin groups' members: never through a job (review 2026-10-13)",
             "resources": targets, "scopes": DENY_SCOPES, "policies": [ref]}
    if not perm:
        steps.append((f"create permission {DENY_PERMISSION}", "POST", f"{authz}/permission/scope", pbody))
    else:
        have_res = sorted(r["name"] for r in api("GET", f"{authz}/policy/{perm['id']}/resources") or [])
        have_scopes = sorted(sc["name"] for sc in api("GET", f"{authz}/policy/{perm['id']}/scopes") or [])
        if have_res != targets or have_scopes != sorted(DENY_SCOPES):
            steps.append((f"update permission {DENY_PERMISSION}", "PUT", f"{authz}/permission/scope/{perm['id']}",
                          dict(pbody, id=perm["id"])))
    return steps


def role_sync_plan(api, authz, policies):
    """The role sync manages the three app clients' roles and nothing else (its realm roles are read-only)."""
    found = {c: api("GET", f"/clients?clientId={c}") for c in [RS_CLIENT] + RS_APPS}
    missing = [c for c, f in found.items() if not f]
    if missing:
        raise SystemExit(f"clients missing in realm nidan: {missing} (import keycloak/realm first)")
    steps = []
    want_clients = [found[RS_CLIENT][0]["id"]]
    pol = policies.get(RS_POLICY)
    body = {"name": RS_POLICY, "type": "client", "logic": "POSITIVE", "decisionStrategy": "UNANIMOUS",
            "description": "the role sync's admin client", "clients": want_clients}
    if not pol:
        steps.append((f"create policy {RS_POLICY}", "POST", f"{authz}/policy/client", body))
        ref = RS_POLICY
    else:
        detail = api("GET", f"{authz}/policy/client/{pol['id']}")
        have = detail.get("clients") or json.loads((detail.get("config") or {}).get("clients") or "[]")
        if sorted(have) != want_clients or detail.get("logic") != "POSITIVE":
            steps.append((f"update policy {RS_POLICY}", "PUT", f"{authz}/policy/client/{pol['id']}", dict(body, id=pol["id"])))
        ref = pol["id"]
    apps = sorted(found[c][0]["id"] for c in RS_APPS)
    perm = policies.get(RS_PERMISSION)
    pbody = {"name": RS_PERMISSION, "type": "scope", "resourceType": "Clients", "decisionStrategy": "UNANIMOUS",
             "description": "role sync: the app clients' roles only", "resources": apps, "scopes": ["manage"],
             "policies": [ref]}
    if not perm:
        steps.append((f"create permission {RS_PERMISSION} on {', '.join(RS_APPS)}", "POST", f"{authz}/permission/scope", pbody))
    else:
        have_res = sorted(r["name"] for r in api("GET", f"{authz}/policy/{perm['id']}/resources") or [])
        have_scopes = sorted(sc["name"] for sc in api("GET", f"{authz}/policy/{perm['id']}/scopes") or [])
        if have_res != apps or have_scopes != ["manage"]:
            steps.append((f"update permission {RS_PERMISSION}", "PUT", f"{authz}/permission/scope/{perm['id']}",
                          dict(pbody, id=perm["id"])))
    return steps


def main(argv):
    args, base = [a for a in argv[1:] if a != "--apply"], "http://127.0.0.1:8180/auth"
    if "--url" in args:
        i = args.index("--url")
        base = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
    if args or not base:
        print(__doc__)
        return 2
    token_file = os.environ.get("KC_TOKEN_FILE", "")
    if not token_file or not Path(token_file).is_file():
        print("set KC_TOKEN_FILE to a file holding the operator's admin access token", file=sys.stderr)
        return 2
    api = Api(base, Path(token_file).read_text().strip())
    try:
        steps = plan(api)
        for what, method, path, body in steps:
            print(("  " if "--apply" in argv else "  would ") + what)
            if "--apply" in argv:
                if body.get("policies") in ([POLICY], [RS_POLICY], [DENY_POLICY]):  # created a step earlier: use its id now
                    authz, name = path.split("/permission/")[0], body["policies"][0]
                    body = dict(body, policies=[next(p["id"] for p in api("GET", f"{authz}/policy?max=500") if p["name"] == name)])
                api(method, path, body)
    except urllib.error.HTTPError as e:
        print(f"Keycloak answered {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
        return 1
    if not steps:
        print("admin permissions: up to date")
    elif "--apply" not in argv:
        print("dry run: nothing changed (add --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
