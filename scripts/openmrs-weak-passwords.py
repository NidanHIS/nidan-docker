#!/usr/bin/env python3
"""Which active OpenMRS accounts still have a known password (backlog KC-33-03, KC-27-06).

  scripts/openmrs-weak-passwords.py [--container nidan-openmrs-db] [--words FILE]

Until 2026-10-09 every provider login CIS created got one shared password (OPENMRS_PROVIDER_DEFAULT_PASSWORD, default
Admin123); new ones now get a random password nobody holds, but existing accounts keep theirs. This lists active
accounts (username, or system id when there is none) whose password is Admin123 or any word in
keycloak/password-blocklist.txt (plus --words). Read only. The comparison runs inside MariaDB (OpenMRS stores
SHA-512(password + salt) as hex), so no password hash leaves the database; it logs in with the DB container's own
MYSQL_USER / MYSQL_PASSWORD (passed by environment, not argv). Exit 1 when any account matches.
Set each listed person's password in OpenMRS (or lock it: apply-links.py lock-plan) before central login is off.
Ceiling: SHA-512 only; accounts still on a legacy SHA-1 hash are not checked.
"""
import subprocess
import sys
from pathlib import Path

BLOCKLIST = Path(__file__).resolve().parent.parent / "keycloak" / "password-blocklist.txt"


def words(extra=None):
    out = ["Admin123"]
    for f in [BLOCKLIST] + ([Path(extra)] if extra else []):
        out += [w.strip() for w in f.read_text(encoding="utf-8").splitlines() if w.strip() and not w.startswith("#")]
    return list(dict.fromkeys(out))


def sql(candidates):
    lit = lambda w: "'" + w.replace("\\", "\\\\").replace("'", "''") + "'"
    tests = " OR ".join(f"password = SHA2(CONCAT({lit(w)}, salt), 512)" for w in candidates)
    return ("SELECT COALESCE(username, system_id) FROM users WHERE retired = 0 AND password IS NOT NULL "
            f"AND salt IS NOT NULL AND ({tests}) ORDER BY 1;")


def main(argv):
    args, container, extra = list(argv[1:]), "nidan-openmrs-db", None
    for flag in ("--container", "--words"):
        if flag in args:
            i = args.index(flag)
            val = args[i + 1] if i + 1 < len(args) else ""
            del args[i:i + 2]
            container, extra = (val, extra) if flag == "--container" else (container, val)
    if args or not container:
        print(__doc__)
        return 2
    r = subprocess.run(["docker", "exec", "-i", container, "sh", "-c",
                        'MYSQL_PWD="$MYSQL_PASSWORD" exec mariadb -N -u"$MYSQL_USER" "$MYSQL_DATABASE"'],
                       input=sql(words(extra)), capture_output=True, text=True)
    if r.returncode != 0:
        print(f"query failed: {r.stderr.strip()[:300]}", file=sys.stderr)
        return 2
    hits = [line for line in r.stdout.splitlines() if line.strip()]
    for h in hits:
        print(f"WEAK  {h}")
    print(f"{len(hits)} active OpenMRS account(s) with a known password")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
