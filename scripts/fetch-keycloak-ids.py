#!/usr/bin/env python3
"""Fill keycloak_sub in a reviewed link table (cutover runbook step 5, backlog KC-36).

  KC_TOKEN_FILE=/secure/token scripts/fetch-keycloak-ids.py WORKBOOK_DIR [--url http://127.0.0.1:8180/auth]

For every row the hospital marked decision=link with an empty keycloak_sub, looks the username up in the
"nidan" realm (admin API, exact match) and writes that user's id. Never changes a decision, never
overwrites an id already filled in; a username with no or several matches is reported, not guessed.
The token is the operator's own admin access token (e.g. from kcadm.sh through the SSH tunnel); it is
read from KC_TOKEN_FILE so it never appears on the command line.
"""
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def lookup(base, token, username, opener=urllib.request.urlopen):
    """List of user ids in realm nidan whose username is exactly *username*."""
    q = urllib.parse.urlencode({"username": username, "exact": "true", "briefRepresentation": "true"})
    req = urllib.request.Request(f"{base.rstrip('/')}/admin/realms/nidan/users?{q}",
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with opener(req, timeout=10) as resp:
        users = json.loads(resp.read().decode())
    return [u["id"] for u in users if str(u.get("username", "")).lower() == username.lower() and UUID.fullmatch(u.get("id", ""))]


def fill(rows, find):
    """(rows, report). *find(username)* returns the matching ids."""
    report = []
    cache = {}
    for r in rows:
        if (r.get("decision") or "").strip().lower() != "link" or (r.get("keycloak_sub") or "").strip():
            continue
        name = (r.get("username") or "").strip().lower()
        if name not in cache:
            cache[name] = find(name)
        ids = cache[name]
        if len(ids) == 1:
            r["keycloak_sub"] = ids[0]
            report.append(f"{name} ({r.get('app')}): {ids[0]}")
        else:
            report.append(f"{name} ({r.get('app')}): {'NOT FOUND in Keycloak' if not ids else 'AMBIGUOUS'} - left blank")
    return rows, report


def main(argv):
    args, base = list(argv[1:]), "http://127.0.0.1:8180/auth"
    if "--url" in args:
        i = args.index("--url")
        base = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
    if len(args) != 1 or not base:
        print(__doc__)
        return 2
    token_file = os.environ.get("KC_TOKEN_FILE", "")
    if not token_file or not Path(token_file).is_file():
        print("set KC_TOKEN_FILE to a file holding the operator's admin access token", file=sys.stderr)
        return 2
    token = Path(token_file).read_text().strip()
    wb = Path(args[0]).resolve()
    if any((d / ".git").exists() for d in [wb, *wb.parents]):
        print("refusing to work inside a git work tree (the workbook holds staff names)", file=sys.stderr)
        return 2
    table = wb / "link-table.csv"
    with open(table, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields, rows = reader.fieldnames, list(reader)
    try:
        rows, report = fill(rows, lambda name: lookup(base, token, name))
    except urllib.error.HTTPError as e:
        print(f"Keycloak answered {e.code} (token expired or not an admin?)", file=sys.stderr)
        return 1
    with open(table, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fields)
        w.writeheader()
        w.writerows(rows)
    for line in report:
        print("  " + line)
    missing = sum("left blank" in line for line in report)
    print(f"{len(report) - missing} id(s) filled, {missing} left blank")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
