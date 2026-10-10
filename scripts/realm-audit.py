#!/usr/bin/env python3
"""Nightly realm audit (backlog KC-09-08, KC-09-09): what the realm files cannot keep true by themselves.

  KC_TOKEN_FILE=/secure/token scripts/realm-audit.py [--url http://127.0.0.1:8180/auth]
  KC_AUTOMATION_CLIENT_SECRET=... scripts/realm-audit.py --client nidan-incident [--disable-expired]   (nightly cron)

Against the live realm "nidan" (admin API, operator token from a file, never on the command line):
  * every member of an admin group or app-superusers has an OTP. An admin without one can be enrolled by whoever
    holds her password, so this window must stay short (new admin, or after a lost phone).
  * nobody is both in an admin group / app-superusers and in a /staff/<job> group (R6).
  * the default role is exactly account/view-profile (Keycloak seeds more; manage-account would let an admin
    delete her own OTP, offline_access would hand out offline tokens).
  * no temporary password (UPDATE_PASSWORD still pending) is older than 7 days: Keycloak has no expiry for them, but
    the Staff page's slip promises one. With --disable-expired those accounts are disabled (an admin re-enables
    and hands out a new slip); without it they are only listed.
Prints one line per finding; exit 1 when there is any (for cron mail / alerting), 0 when clean.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ADMIN_GROUPS = ["/hospital-admin", "/cis-admin", "/orthanc-admin", "/app-superusers"]
TEMPORARY_DAYS = 7


class Api:
    def __init__(self, base, token, opener=urllib.request.urlopen):
        self.base, self.token, self.opener = base.rstrip("/") + "/admin/realms/nidan", token, opener

    def get(self, path):
        req = urllib.request.Request(self.base + path, headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"})
        with self.opener(req, timeout=15) as resp:
            return json.loads(resp.read().decode() or "null")

    def put(self, path, body):
        req = urllib.request.Request(self.base + path, method="PUT", data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        with self.opener(req, timeout=15) as resp:
            resp.read()


def members(api, group_id):
    out, first = [], 0
    while True:
        page = api.get(f"/groups/{group_id}/members?first={first}&max=200&briefRepresentation=true")
        out += page
        if len(page) < 200:
            return out
        first += 200


def expired_temporary(api, now_ms):
    """[(id, username, days)] for enabled users whose temporary password is older than TEMPORARY_DAYS."""
    out, first = [], 0
    while True:
        page = api.get(f"/users?first={first}&max=200&briefRepresentation=false")
        for u in page:
            if not u.get("enabled", True) or "UPDATE_PASSWORD" not in (u.get("requiredActions") or []):
                continue
            pw = [c for c in api.get(f"/users/{u['id']}/credentials") if c.get("type") == "password"]
            if pw and pw[0].get("createdDate") is not None:
                days = (now_ms - pw[0]["createdDate"]) / 86_400_000
                if days > TEMPORARY_DAYS:
                    out.append((u["id"], u["username"], int(days)))
        if len(page) < 200:
            return out
        first += 200


def findings(api, now_ms=None, disable=False):
    out = []
    for uid, username, days in expired_temporary(api, now_ms or time.time() * 1000):
        if disable:
            api.put(f"/users/{uid}", {"enabled": False})
        out.append(f"{username}: temporary password is {days} days old" + (" - disabled" if disable else " (--disable-expired)"))
    groups = {g["path"]: g for g in api.get("/groups?briefRepresentation=true")}
    admins = {}
    for path in ADMIN_GROUPS:
        if path not in groups:
            out.append(f"group {path} is missing")
            continue
        for u in members(api, groups[path]["id"]):
            admins.setdefault(u["id"], (u["username"], []))[1].append(path)
    for uid, (username, paths) in sorted(admins.items(), key=lambda x: x[1][0]):
        if not any(c.get("type") == "otp" for c in api.get(f"/users/{uid}/credentials")):
            out.append(f"{username} ({', '.join(paths)}): no OTP enrolled")
    if "/staff" in groups:
        for job in api.get(f"/groups/{groups['/staff']['id']}/children?max=200"):
            for u in members(api, job["id"]):
                if u["id"] in admins:
                    out.append(f"{u['username']}: in {job['path']} and in {', '.join(admins[u['id']][1])} (R6)")
    composites = api.get("/roles/default-roles-nidan/composites")
    names = sorted(f"{'client:' if r.get('clientRole') else ''}{r['name']}" for r in composites)
    if names != ["client:view-profile"]:
        out.append(f"default role is {names}, expected exactly [client:view-profile]")
    return out


def main(argv):
    args, base = [a for a in argv[1:] if a != "--disable-expired"], "http://127.0.0.1:8180/auth"
    if "--url" in args:
        i = args.index("--url")
        base = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
    client = None
    if "--client" in args:
        i = args.index("--client")
        client = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
    if args or not base or client == "":
        print(__doc__)
        return 2
    try:
        if client:   # master-realm automation client, secret from the environment only
            secret = os.environ.get("KC_AUTOMATION_CLIENT_SECRET", "")
            if not secret:
                print("set KC_AUTOMATION_CLIENT_SECRET (from .env.local)", file=sys.stderr)
                return 2
            req = urllib.request.Request(base.rstrip("/") + "/realms/master/protocol/openid-connect/token",
                                         data=urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": client,
                                                                      "client_secret": secret}).encode())
            with urllib.request.urlopen(req, timeout=15) as resp:
                token = json.loads(resp.read().decode())["access_token"]
        else:
            token_file = os.environ.get("KC_TOKEN_FILE", "")
            if not token_file or not Path(token_file).is_file():
                print("set KC_TOKEN_FILE to a file holding an admin access token, or use --client", file=sys.stderr)
                return 2
            token = Path(token_file).read_text().strip()
        out = findings(Api(base, token), disable="--disable-expired" in argv)
    except urllib.error.HTTPError as e:
        print(f"Keycloak answered {e.code}", file=sys.stderr)
        return 2
    for line in out:
        print("  " + line)
    print(f"realm audit: {len(out)} finding(s)")
    return 1 if out else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
