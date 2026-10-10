"""Snapshot of the realm harness (keycloak.yml) through the admin API, for the import idempotence check (KC-01-01).

  python3 -I tests/e2e-auth/harness/realm-snapshot.py OUT.json
"""
import json, sys, urllib.request, urllib.parse
B = "http://127.0.0.1:18180/auth"
tok = json.load(urllib.request.urlopen(urllib.request.Request(B + "/realms/master/protocol/openid-connect/token",
      data=urllib.parse.urlencode({"grant_type": "password", "client_id": "admin-cli", "username": "admin",
      "password": "harness-admin-password"}).encode())))["access_token"]
def get(p):
    return json.load(urllib.request.urlopen(urllib.request.Request(B + "/admin/realms" + p, headers={"Authorization": "Bearer " + tok})))
out = {}
for realm in ("nidan", "master"):
    r = get("/" + realm)
    out[realm] = {"realm": r, "clients": get(f"/{realm}/clients"), "groups": get(f"/{realm}/groups?briefRepresentation=false"),
                  "roles": get(f"/{realm}/roles?briefRepresentation=false"),
                  "components": get(f"/{realm}/components"), "flows": get(f"/{realm}/authentication/flows")}
    if realm == "nidan":
        out[realm]["profile"] = get("/nidan/users/profile")
        out[realm]["staff"] = get("/nidan/groups/" + [g for g in out[realm]["groups"] if g["name"] == "staff"][0]["id"] + "/children?briefRepresentation=false")
def norm(x):
    """Keycloak returns some string lists (e.g. a client's default scopes) in no fixed order: order is not drift."""
    if isinstance(x, dict):
        return {k: norm(v) for k, v in x.items()}
    if isinstance(x, list):
        items = [norm(v) for v in x]
        return sorted(items) if all(isinstance(v, (str, int, float, bool)) for v in items) else items
    return x
json.dump(norm(out), open(sys.argv[1], "w"), indent=1, sort_keys=True)
print("snapshot", sys.argv[1], sum(len(v["clients"]) for v in out.values()), "clients")
