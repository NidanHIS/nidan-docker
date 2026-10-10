#!/usr/bin/env python3
"""Hospital-admin scope inside Keycloak (backlog KC-09): fine-grained admin permissions v2 for the Staff page.

  KC_TOKEN_FILE=/secure/token scripts/keycloak-admin-permissions.py [--apply] [--url http://127.0.0.1:8180/auth]

Keycloak stores these permissions by internal ids (groups, resources, scopes), which differ per site, so they
cannot live in the realm YAML; the realm file only turns the feature on (adminPermissionsEnabled). This script
resolves the groups by path and makes realm "nidan" hold exactly:
  policy      is-hospital-admin            member of /hospital-admin
  permission  hospital-admin-staff-groups  Groups = every /staff/<job> group: view, view-members, manage-members,
                                           manage-membership, manage-membership-of-members
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
                if body.get("policies") == [POLICY]:  # the policy was created a step earlier: use its id now
                    authz = path.split("/permission/")[0]
                    body = dict(body, policies=[next(p["id"] for p in api("GET", f"{authz}/policy?max=500") if p["name"] == POLICY)])
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
