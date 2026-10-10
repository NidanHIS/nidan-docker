"""Self-test for keycloak-admin-permissions.py: `python3 -I scripts/test_keycloak_admin_permissions.py`.

A fake of the admin API's few endpoints; the live behaviour is tested by tests/e2e-auth/specs/realm.spec.ts.
"""
import importlib.util
import json
import uuid
from pathlib import Path

spec = importlib.util.spec_from_file_location("kap", Path(__file__).resolve().parent / "keycloak-admin-permissions.py")
kap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kap)


class Fake:
    def __init__(self, jobs=("Nurse", "Cashier"), enabled=True):
        self.enabled = enabled
        self.groups = [{"id": "g-ha", "name": "hospital-admin", "path": "/hospital-admin"},
                       {"id": "g-st", "name": "staff", "path": "/staff"},
                       {"id": "g-su", "name": "app-superusers", "path": "/app-superusers"}]
        self.jobs = [{"id": f"j-{j}", "name": j, "path": f"/staff/{j}"} for j in jobs]
        self.jobs.append({"id": "j-x", "name": "odd", "path": "/elsewhere/odd"})   # not a /staff/<name> child: ignored
        self.policies = {}
        self.writes = []

    def __call__(self, method, path, body=None):
        A = "/clients/c1/authz/resource-server"
        if method == "GET" and path == "":
            return {"adminPermissionsEnabled": self.enabled}
        if path.startswith("/clients?clientId=admin-permissions"):
            return [{"id": "c1"}]
        if path.startswith("/groups?"):
            return self.groups
        if path.startswith("/groups/g-st/children"):
            return self.jobs
        if method == "GET" and path.startswith(A + "/policy?"):
            return [{"id": p["id"], "name": p["name"]} for p in self.policies.values()]
        if method == "GET" and path.startswith(A + "/policy/group/"):
            p = self.policies[path.rsplit("/", 1)[1]]
            return {"id": p["id"], "logic": p["logic"], "groups": p["groups"]}
        if method == "GET" and path.startswith(A + "/permission/scope/"):
            return {"decisionStrategy": self.policies[path.rsplit("/", 1)[1]]["decisionStrategy"]}
        if method == "GET" and path.startswith(A + "/policy/"):
            pid, what = path[len(A) + 8:].split("/")
            p = self.policies[pid]
            return {"resources": [{"name": r, "_id": "res-" + r} for r in p["resources"]],
                    "scopes": [{"name": s} for s in p["scopes"]],
                    "associatedPolicies": [{"id": x} for x in p["policies"]]}[what]
        if method in ("POST", "PUT"):
            self.writes.append((method, path))
            pid = body.get("id") or str(uuid.uuid4())
            self.policies[pid] = dict(body, id=pid)
            return None
        raise AssertionError((method, path))


f = Fake()
steps = kap.plan(f)
assert [s[1] for s in steps] == ["POST", "POST"], steps
assert "Cashier, Nurse" in steps[1][0] and "odd" not in steps[1][0], steps
# apply the plan the way main() does (policy id resolved after creation)
for what, method, path, body in steps:
    if body.get("policies") == [kap.POLICY]:
        body = dict(body, policies=[next(p["id"] for p in f.policies.values() if p["name"] == kap.POLICY)])
    f(method, path, body)
perm = next(p for p in f.policies.values() if p["name"] == kap.PERMISSION)
assert sorted(perm["resources"]) == ["j-Cashier", "j-Nurse"] and perm["resourceType"] == "Groups"
assert perm["scopes"] == kap.SCOPES and "reset-password" not in perm["scopes"]
pol = next(p for p in f.policies.values() if p["name"] == kap.POLICY)
assert pol["groups"] == [{"id": "g-ha", "extendChildren": False}]
assert kap.plan(f) == [], kap.plan(f)                                  # idempotent

f.jobs.insert(0, {"id": "j-Doctor", "name": "Doctor", "path": "/staff/Doctor"})
again = kap.plan(f)
assert [(s[1], "Doctor" in s[0]) for s in again] == [("PUT", True)], again   # new job -> one update

try:
    kap.plan(Fake(enabled=False))
    raise AssertionError("must refuse a realm without adminPermissionsEnabled")
except SystemExit:
    pass
print("keycloak-admin-permissions: OK")
