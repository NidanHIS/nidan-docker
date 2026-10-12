#!/usr/bin/env python3
"""Manual-testing users on the full harness (tests/e2e-auth/harness/sso-openmrs.yml). Idempotent; re-run after any
Keycloak restart (the harness realm is dev-mem). Harness only: throwaway password, never on a real site.

  python3 -I tests/e2e-auth/harness/manual-users.py               harness callbacks + the users below
  python3 -I tests/e2e-auth/harness/manual-users.py --fixups-only  harness callbacks only (run-keycloak-suite.sh)

Harness callbacks: the realm registers the gateway's https://<domain>:8443 and :9443 callbacks, as production
publishes them; the harness publishes those ports as 18444 and 18445, so their callbacks are added to nidan-gateway.
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

KC = "http://localhost:18180/auth"
PASSWORD = "Harness-Pass-123"
USERS = [
    # username, first, last, gender, groups, {client: [roles]}, attributes
    ("nurse.manual", "Sita", "Nurse", "F", ["/staff/Nurse"],
     {"nidan-gateway": ["app-openmrs"], "openmrs": ["Organizational: Nurse", "Application: Enters Vitals",
                                                    "Application: Uses Patient Summary"]}, {}),
    ("doctor.manual", "Ram", "Doctor", "M", ["/staff/Doctor"],
     {"nidan-gateway": ["app-openmrs", "app-orthanc", "app-superset"],
      "openmrs": ["Organizational: Doctor", "Application: Writes Clinical Notes", "Application: Uses Patient Summary"],
      "superset": ["Gamma", "Clinical"]}, {}),
    ("registration.manual", "Gita", "Clerk", "F", ["/staff/Nurse"],
     {"nidan-gateway": ["app-openmrs"], "openmrs": ["Organizational: Registration Clerk", "Application: Registers Patients"]}, {}),
    ("cashier.manual", "Hari", "Cashier", "M", ["/staff/Cashier"],
     {"nidan-gateway": ["app-odoo"], "odoo": ["point_of_sale.group_pos_user"]}, {"nidan_shops": ["Harness Shop A"]}),
    ("labtech.manual", "Maya", "Labtech", "F", ["/staff/Lab Tech"],
     {"nidan-gateway": ["app-openelis"], "openelis": ["Results", "Validation", "Reception"]}, {}),
    ("reports.manual", "Bikash", "Reports", "M", ["/staff/Nurse"],
     {"nidan-gateway": ["app-superset"], "superset": ["Gamma"]}, {}),
    ("hadmin.manual", "Anita", "Admin", "F", ["/hospital-admin"], {}, {}),
    ("staffadmin.manual", "Sunita", "Staffadmin", "F", ["/hospital-admin"], {}, {}),
    ("noaccess.manual", "Kiran", "Noaccess", "M", ["/staff/Nurse"], {}, {}),
    # people with several apps (and not the others)
    ("clinician.manual", "Pooja", "Clinician", "F", ["/staff/Doctor"],
     {"nidan-gateway": ["app-openmrs", "app-openelis", "app-superset"],
      "openmrs": ["Organizational: Doctor", "Application: Writes Clinical Notes"], "openelis": ["Results", "Validation"],
      "superset": ["Gamma", "Clinical"]}, {}),
    ("frontdesk.manual", "Suman", "Frontdesk", "M", ["/staff/Cashier"],
     {"nidan-gateway": ["app-openmrs", "app-odoo"], "openmrs": ["Organizational: Registration Clerk",
      "Application: Registers Patients"], "odoo": ["point_of_sale.group_pos_user"]}, {"nidan_shops": ["Harness Shop A"]}),
    ("labcashier.manual", "Rita", "Labcashier", "F", ["/staff/Lab Tech"],
     {"nidan-gateway": ["app-openelis", "app-odoo"], "openelis": ["Reception"], "odoo": ["point_of_sale.group_pos_user"]},
     {"nidan_shops": ["Harness Shop B"]}),
]


def call(token, method, path, data=None):
    req = urllib.request.Request(KC + "/admin/realms/nidan" + path, method=method,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        body = r.read()
        return r.status, (json.loads(body) if body else None), r.headers.get("Location")


HARNESS_CALLBACKS = {"nidan-gateway": ["https://localhost:18444/oauth2/callback", "https://localhost:18445/oauth2/callback"]}


def fixups(token):
    for client_id, uris in HARNESS_CALLBACKS.items():
        c = call(token, "GET", f"/clients?clientId={client_id}")[1][0]
        missing = [u for u in uris if u not in c.get("redirectUris", [])]
        if missing:
            call(token, "PUT", f"/clients/{c['id']}", {**c, "redirectUris": c["redirectUris"] + missing})
        print(f"{client_id}: harness callbacks {'added' if missing else 'present'}")


def main():
    token = json.load(urllib.request.urlopen(urllib.request.Request(
        KC + "/realms/master/protocol/openid-connect/token", data=urllib.parse.urlencode(
            {"grant_type": "password", "client_id": "admin-cli", "username": "admin",
             "password": "harness-admin-password"}).encode())))["access_token"]
    fixups(token)
    if "--fixups-only" in sys.argv:
        return 0
    clients = {}
    for username, first, last, gender, groups, roles, attrs in USERS:
        found = call(token, "GET", f"/users?username={urllib.parse.quote(username)}&exact=true")[1]
        rep = {"username": username, "firstName": first, "lastName": last, "enabled": True,
               "email": f"{username}@example.org", "emailVerified": True,
               "attributes": {"gender": [gender], **attrs}}
        if found:
            uid = found[0]["id"]
            call(token, "PUT", f"/users/{uid}", rep)
        else:
            uid = call(token, "POST", "/users", {**rep, "groups": groups})[2].rsplit("/", 1)[1]
        call(token, "PUT", f"/users/{uid}/reset-password", {"type": "password", "value": PASSWORD, "temporary": False})
        for client, names in roles.items():
            if client not in clients:
                clients[client] = call(token, "GET", f"/clients?clientId={client}")[1][0]["id"]
            cid = clients[client]
            reps = []
            for n in names:
                try:
                    reps.append(call(token, "GET", f"/clients/{cid}/roles/{urllib.parse.quote(n)}")[1])
                except urllib.error.HTTPError:    # the harness realm has no full catalog: add the role
                    call(token, "POST", f"/clients/{cid}/roles", {"name": n})
                    reps.append(call(token, "GET", f"/clients/{cid}/roles/{urllib.parse.quote(n)}")[1])
            call(token, "POST", f"/users/{uid}/role-mappings/clients/{cid}", reps)
        print(f"{username:20s} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
