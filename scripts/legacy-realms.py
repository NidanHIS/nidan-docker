#!/usr/bin/env python3
"""Realms left over from the old compose (backlog KC-04-02).

  KC_TOKEN_FILE=/secure/token scripts/legacy-realms.py [--url http://127.0.0.1:8180/auth]            list
  KC_TOKEN_FILE=/secure/token scripts/legacy-realms.py --delete REALM [--url ...]                    remove one

The old compose started Keycloak with --import-realm on keycloak/imports/realm-export.json ("nidan-realm", with an admin
user and password) and on openelis/keycloak/. keycloak-config-cli manages only master and nidan and never removes a
realm, so a Keycloak database carried over from such a site still serves those realms (/auth/realms/nidan-realm).
This lists every realm other than master and nidan (exit 1 when there is any) and, with --delete REALM, removes that
one realm, never master or nidan. Take a backup first (scripts/volume-backup.sh backup <dir> keycloak-db-data):
deleting a realm cannot be undone. Needs an operator's master-realm admin token from a file (through the SSH tunnel).
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

KEEP = {"master", "nidan"}


def call(base, token, method, path):
    req = urllib.request.Request(base.rstrip("/") + "/admin/realms" + path, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode()
        return json.loads(body) if body else None


def legacy(realms):
    return sorted(r["realm"] for r in realms if r["realm"] not in KEEP)


def main(argv, call=call):
    args, base, delete = list(argv[1:]), "http://127.0.0.1:8180/auth", None
    for flag in ("--url", "--delete"):
        if flag in args:
            i = args.index(flag)
            val = args[i + 1] if i + 1 < len(args) else ""
            del args[i:i + 2]
            base, delete = (val, delete) if flag == "--url" else (base, val)
    if args or delete == "":
        print(__doc__)
        return 2
    tf = os.environ.get("KC_TOKEN_FILE", "")
    if not tf or not Path(tf).is_file():
        print("set KC_TOKEN_FILE to a file holding a master-realm admin access token", file=sys.stderr)
        return 2
    token = Path(tf).read_text().strip()
    try:
        found = legacy(call(base, token, "GET", "?briefRepresentation=true"))
        if delete:
            if delete in KEEP:
                print(f"refusing: {delete} is not a legacy realm", file=sys.stderr)
                return 2
            if delete not in found:
                print(f"no legacy realm {delete!r} here (found: {found or 'none'})", file=sys.stderr)
                return 2
            call(base, token, "DELETE", "/" + delete)
            print(f"deleted realm {delete}")
            found = legacy(call(base, token, "GET", "?briefRepresentation=true"))
    except urllib.error.HTTPError as e:
        print(f"Keycloak answered {e.code}", file=sys.stderr)
        return 1
    for r in found:
        print(f"LEGACY  {r}   (back up keycloak-db-data, then: scripts/legacy-realms.py --delete {r})")
    print(f"{len(found)} legacy realm(s); realms should be exactly master and nidan")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
