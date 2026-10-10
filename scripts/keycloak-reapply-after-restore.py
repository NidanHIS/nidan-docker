#!/usr/bin/env python3
"""After restoring Keycloak from a backup, re-apply the revocations made since that backup (backlog KC-28-07).

  KC_TOKEN_FILE=/secure/token scripts/keycloak-reapply-after-restore.py EVENTS_DIR --since <backup time> [--apply]
        [--url http://127.0.0.1:8180/auth]       (<backup time>: epoch ms, or ISO 8601 like 2026-10-09T02:00:00Z)

A restored database is as it was at backup time: anyone disabled, removed from a job or deleted after it is back.
This reads the admin events shipped by keycloak-event-shipper.py (EVENTS_DIR/admin-events-*.jsonl) newer than the
backup and re-applies only the ones that TAKE ACCESS AWAY, oldest first:
  UPDATE USER with enabled=false          -> disable the user again
  DELETE GROUP_MEMBERSHIP                  -> remove that membership again
  DELETE USER                              -> disable the user (an operator removes her afterwards if wanted)
Nothing is ever granted back. Users or groups that no longer exist are reported and skipped. Dry run (prints the
plan) unless --apply. Run it BEFORE the restored Keycloak is reachable by staff (cutover/restore runbook).
"""
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def parse_since(text):
    if re.fullmatch(r"\d+", text):
        return int(text)
    return int(datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def revocations(events_dir, since_ms):
    """[(time, action, user_id, group_id or None)] from shipped admin events newer than since_ms, oldest first."""
    out = []
    for f in sorted(Path(events_dir).glob("admin-events-*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e.get("time", 0) <= since_ms or (e.get("realmId") and e.get("realmName", "nidan") != "nidan"):
                continue
            path, op, kind = e.get("resourcePath") or "", e.get("operationType"), e.get("resourceType")
            m = re.fullmatch(rf"users/({ID})(?:/groups/({ID}))?", path)
            if not m:
                continue
            if kind == "USER" and op == "UPDATE":
                try:
                    rep = json.loads(e.get("representation") or "{}")
                except ValueError:
                    rep = {}
                if rep.get("enabled") is False and not m.group(2):
                    out.append((e["time"], "disable", m.group(1), None))
            elif kind == "USER" and op == "DELETE" and not m.group(2):
                out.append((e["time"], "disable", m.group(1), None))
            elif kind == "GROUP_MEMBERSHIP" and op == "DELETE" and m.group(2):
                out.append((e["time"], "leave-group", m.group(1), m.group(2)))
    return sorted(out)


def main(argv):
    args, base = [a for a in argv[1:] if a != "--apply"], "http://127.0.0.1:8180/auth"
    since = None
    for flag in ("--url", "--since"):
        if flag in args:
            i = args.index(flag)
            val = args[i + 1] if i + 1 < len(args) else ""
            del args[i:i + 2]
            if flag == "--url":
                base = val
            else:
                since = val
    if len(args) != 1 or not since:
        print(__doc__)
        return 2
    tf = os.environ.get("KC_TOKEN_FILE", "")
    if not tf or not Path(tf).is_file():
        print("set KC_TOKEN_FILE to a file holding an admin access token", file=sys.stderr)
        return 2
    token = Path(tf).read_text().strip()

    def call(method, path, body=None):
        req = urllib.request.Request(base.rstrip("/") + "/admin/realms/nidan" + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status

    steps = revocations(args[0], parse_since(since))
    apply = "--apply" in argv
    done = skipped = 0
    for t, action, user, group in steps:
        when = datetime.datetime.fromtimestamp(t / 1000, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        what = f"{when} {action} user {user}" + (f" group {group}" if group else "")
        if not apply:
            print("  would " + what)
            continue
        try:
            if action == "disable":
                call("PUT", f"/users/{user}", {"enabled": False})
            else:
                call("DELETE", f"/users/{user}/groups/{group}")
            print("  " + what)
            done += 1
        except urllib.error.HTTPError as e:
            print(f"  skipped ({e.code}): {what}")
            skipped += 1
    if apply:
        print(f"reapplied {done} revocation(s), {skipped} skipped")
    else:
        print(f"{len(steps)} revocation(s) to re-apply; dry run (add --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
