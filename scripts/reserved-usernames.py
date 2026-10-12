#!/usr/bin/env python3
"""Reserved Keycloak usernames -> the realm's username pattern (backlog KC-55-03/04, D21).

  scripts/reserved-usernames.py [--env-file F]... [--site-file reserved-usernames.txt]

Prints one line, KC_USERNAME_PATTERN=<java regex> ("$" doubled for compose), for .env.local. The realm user profile
(keycloak/realm/base/nidan.yaml) validates every username against it, so Keycloak refuses:
  * fixed names (admin, nidan, nidan-sync, nidan-odoo, nidan-portal, daemon, __system__, public, default, ...)
  * every client id in keycloak/realm/** and any cis-* / ois-* name
  * every username-like value in the env files (*_USERNAME, *_USER, *_ADMIN_USER)
  * the OpenMRS system-id shape (^[0-9]+-[0-9]$) and UUIDs (an SSO user's sub)
  * every local-only username from the site export (bootstrap-export.py workbook)
and requires 3-40 chars [a-z0-9._-] starting with a letter. Default env files: .env then .env.local.
"""
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REALM_DIR = HERE.parent / "keycloak" / "realm"
FIXED = {"admin", "administrator", "root", "nidan", "nidan-sync", "nidan-odoo", "nidan-portal", "nidan-rolesync", "daemon", "__system__", "system", "public",
         "default", "guest", "anonymous", "service-account", "openmrs", "odoo", "openelis", "superset", "dhis2",
         "orthanc", "keycloak", "dead-letter-admin"}
USER_KEY = re.compile(r"(_USERNAME|_USER|_ADMIN_USER)$")
SHAPES = [r"[0-9]+-[0-9]", r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"]
PREFIXES = ["cis-", "ois-", "service-account-"]
BASE = r"[a-z][a-z0-9._-]{2,39}"


def env_names(files):
    out = set()
    for f in files:
        if Path(f).is_file():
            for line in Path(f).read_text(encoding="utf-8").splitlines():
                k, sep, v = line.partition("=")
                v = v.strip().strip('"').strip("'")
                if sep and not k.lstrip().startswith("#") and USER_KEY.search(k.strip()) and v and "$" not in v:
                    out.add(v.lower())
    return out


def client_ids():
    out = set()
    for f in REALM_DIR.rglob("*.yaml"):
        for c in (yaml.safe_load(f.read_text()) or {}).get("clients") or []:
            if c.get("clientId"):
                out.add(c["clientId"].lower())
    return out


def reserved(env_files, site_file=None):
    names = FIXED | client_ids() | env_names(env_files)
    if site_file:
        names |= {l.strip().lower() for l in Path(site_file).read_text(encoding="utf-8").splitlines() if l.strip()}
    return names


def lit(name):
    # No backslashes: the pattern is substituted into a JSON string in the realm file. "." is the
    # only metacharacter a valid username can hold; "-" and "_" are literal outside a class.
    return name.replace(".", "[.]")


def pattern(names):
    """Java/Python-compatible regex (lookaheads and classes only) that refuses every name."""
    valid = sorted(n for n in names if re.fullmatch(r"[a-z0-9._-]+", n))  # anything else fails BASE anyway
    looks = ["(?!(?:" + "|".join(lit(n) for n in valid) + ")$)"]
    looks += [f"(?!{s}$)" for s in SHAPES] + [f"(?!{lit(p)})" for p in PREFIXES]
    return "^" + "".join(looks) + BASE + "$"


def main(argv):
    env_files, site = [], None
    it = iter(argv[1:])
    for a in it:
        if a == "--env-file":
            env_files.append(next(it))
        elif a == "--site-file":
            site = next(it)
        else:
            print(__doc__)
            return 2
    env_files = env_files or [str(HERE.parent / ".env"), str(HERE.parent / ".env.local")]
    names = reserved(env_files, site)
    print(f"# {len(names)} reserved usernames" + (" (incl. site export)" if site else " (NO site export given)"),
          file=sys.stderr)
    # compose interpolates "$" in env files: write it as "$$"
    print("KC_USERNAME_PATTERN=" + pattern(names).replace("$", "$$"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
