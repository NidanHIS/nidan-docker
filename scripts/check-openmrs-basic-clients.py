#!/usr/bin/env python3
"""Inventory of OpenMRS Basic-auth clients across the monorepo (backlog KC-32-06).

  scripts/check-openmrs-basic-clients.py [MONOREPO_ROOT]

A Basic-auth caller of OpenMRS has to read an OpenMRS password, so every source/config file that
names one (OPENMRS_PASSWORD, OPENMRS_ADMIN_PASSWORD, OPENMRS_FHIR_PASSWORD, NIDAN_SYNC_PASSWORD, FHIR(2)_PASSWORD,
openmrs.password, openmrs_password) must be listed in security/openmrs-basic-clients.allow with
the story that moves it to a client-credentials token. Fails on an unlisted file (a new Basic
client) and on a listed file that no longer reads one (remove the line: ratchet). Only files git
tracks or would add (not ignored) count; docs, tests and perf scripts are skipped. Repos that are not checked out (CI of a single repo) are reported, not
failed.
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALLOW = HERE.parent / "security" / "openmrs-basic-clients.allow"
REPOS = ["nidan-docker", "nidan-middleware", "odoo_19_addons", "nidan_odoo_extra_addons", "nidan-patient-portal",
         "nidan-dhis2-integration", "nidan_seed_etl", "hmis-query", "openmrs-frontend",
         "openmrs-backend/openmrs-module-nidancore", "OpenELIS-Global-2/src/main"]
CRED = re.compile(r"OPENMRS_(ADMIN_|FHIR_)?PASSWORD\b|NIDAN_SYNC_PASSWORD|openmrs[._]password\b|FHIR2?_PASSWORD", re.I)
SKIP_DIRS = {"node_modules", "target", "dist", "build", ".git", "test", "tests", "perf", "__pycache__", "e2e-auth"}
SKIP_SUFFIX = {".md", ".png", ".jpg", ".svg", ".tgz", ".omod", ".jar", ".lock", ".allow", ".pyc"}


def scan(root):
    root = Path(root).resolve()  # git reports resolved paths (e.g. macOS /var -> /private/var)
    found, missing = set(), []
    for repo in REPOS:
        base = root / repo
        if not base.is_dir():
            missing.append(repo)
            continue
        listed = subprocess.run(["git", "-C", str(base), "ls-files", "-z", "--full-name", "--cached", "--others",
                                 "--exclude-standard", "."],
                                capture_output=True, text=True)
        if listed.returncode != 0:
            missing.append(repo + " (not a git checkout)")
            continue
        top = subprocess.run(["git", "-C", str(base), "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True).stdout.strip()
        for name in filter(None, listed.stdout.split("\0")):
            f = Path(top) / name
            rel = f.relative_to(root)
            if f.resolve() == Path(__file__).resolve():
                continue
            if not f.is_file() or f.suffix in SKIP_SUFFIX or SKIP_DIRS & set(rel.parts[:-1]) or f.name.startswith("test_"):
                continue
            try:
                if CRED.search(f.read_text(encoding="utf-8", errors="ignore")):
                    found.add(rel.as_posix())
            except OSError:
                pass
    return found, missing


def read_allow():
    out = {}
    for line in ALLOW.read_text(encoding="utf-8").splitlines() if ALLOW.exists() else []:
        if line.strip() and not line.startswith("#"):
            path, _, why = line.partition(" ")
            out[path] = why.strip()
    return out


def check(root):
    found, missing = scan(root)
    allow = read_allow()
    present = {p for p in allow if not any(p.startswith(m + "/") for m in missing)}
    new = sorted(found - set(allow))
    stale = sorted(present - found)
    return new, stale, missing, found


def main(argv):
    root = Path(argv[0]).resolve() if argv else HERE.parent.parent
    new, stale, missing, found = check(root)
    print(f"OpenMRS Basic-auth clients: {len(found)} file(s) read an OpenMRS password; {len(new)} new, {len(stale)} stale")
    for p in new:
        print(f"  NEW   {p}  (move it to a client-credentials token, or list it in {ALLOW.name} with its story)")
    for p in stale:
        print(f"  STALE {p}  (no longer reads an OpenMRS password: remove it from {ALLOW.name})")
    if missing:
        print("  not checked out here: " + ", ".join(missing))
    return 1 if new or stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
