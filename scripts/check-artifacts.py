#!/usr/bin/env python3
"""Committed build artifacts must be accounted for (backlog KC-46-03).

  scripts/check-artifacts.py          CI: every committed .omod/.tgz/.jar/.war is in the manifest with
                                      the same sha256, and nothing stale is listed
  scripts/check-artifacts.py --write  add new artifacts / refresh sha256 (keeps hand-filled fields)

security/artifacts.manifest.yaml per artifact: sha256, added_in (nidan-docker commit), source (dir in
the monorepo, or "not in monorepo"), source_commit (filled by whoever builds it; "unknown" until
then), auth_relevant. A binary swapped without updating the manifest fails CI. Unknown provenance
is reported (auth-relevant ones loudly); rebuild-and-compare for those is the next step.
"""
import hashlib
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "security" / "artifacts.manifest.yaml"
PATTERNS = ["*.omod", "*.tgz", "*.jar", "*.war"]
# artifact name prefix -> source dir in the monorepo
SOURCES = {
    "appointments-": "openmrs-backend/openmrs-module-appointments", "attachments-": "openmrs-backend/openmrs-module-attachments",
    "bahmni-ipd-": "openmrs-backend/openmrs-module-ipd", "bedmanagement-": "openmrs-backend/openmrs-module-bedmanagement",
    "communication-": "openmrs-backend/openmrs-module-communication", "fhir2-": "openmrs-backend/openmrs-module-fhir2",
    "legacyui-": "openmrs-backend/openmrs-module-legacyui", "nidancore-": "openmrs-backend/openmrs-module-nidancore",
    "medication-administration-": "openmrs-backend/openmrs-module-medicationadministration",
    "orderexpansion-": "openmrs-backend/openmrs-module-orderexpansion", "oauth2login-": "openmrs-backend/openmrs-module-oauth2login",
    "teleconsultation-": "openmrs-backend/openmrs-module-teleconsultation",
    "openmrs-esm-patient-": "openmrs-frontend/openmrs-esm-patient-chart", "openmrs-esm-form-builder-": "openmrs-frontend/openmrs-esm-form-builder",
    "openmrs-esm-form-engine-": "openmrs-frontend/openmrs-esm-form-engine-lib", "nidan-esm-nepali-calendar-": "openmrs-frontend/nidan-esm-nepali-calendar",
    "trigonal-esm-ipd-app-": "openmrs-frontend/trigonal-esm-ipd-app",
}
# Login, session, paywall: a tampered build here is an auth bypass.
AUTH_RELEVANT = ("oauth2login-", "openmrs-esm-login-app-", "openmrs-esm-paywall-app-", "nidancore-", "fhir2-")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def artifacts():
    names = git("ls-files", "--cached", "--others", "--exclude-standard", "--", *PATTERNS).splitlines()
    return sorted(n for n in names if (ROOT / n).is_file())


def sha256(path):
    h = hashlib.sha256()
    with open(ROOT / path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load():
    return (yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}).get("artifacts", {}) if MANIFEST.exists() else {}


def entry(path, old):
    name = Path(path).name
    src = next((v for k, v in SOURCES.items() if name.startswith(k)), "not in monorepo")
    return {
        "sha256": sha256(path),
        "added_in": old.get("added_in") or git("log", "--diff-filter=A", "--format=%h", "-1", "--", path) or "uncommitted",
        "source": old.get("source") or src,
        "source_commit": old.get("source_commit") or "unknown",
        "auth_relevant": name.startswith(AUTH_RELEVANT),
    }


def check(found, manifest):
    problems = []
    for p in found:
        m = manifest.get(p)
        if not m:
            problems.append(f"{p}: not in the manifest")
        elif m.get("sha256") != sha256(p):
            problems.append(f"{p}: sha256 differs from the manifest (binary changed without review)")
    problems += [f"{p}: listed but no longer committed" for p in sorted(set(manifest) - set(found))]
    return problems


def main(argv):
    found, manifest = artifacts(), load()
    if "--write" in argv:
        new = {p: entry(p, manifest.get(p, {})) for p in found}
        MANIFEST.write_text("# Committed build artifacts (backlog KC-46-03). scripts/check-artifacts.py --write, then fill\n"
                            "# source_commit for each from the build that produced it.\n"
                            + yaml.safe_dump({"artifacts": new}, sort_keys=True), encoding="utf-8")
        print(f"manifest: {len(new)} artifact(s)")
        return 0
    problems = check(found, manifest)
    unknown = [p for p in found if (manifest.get(p) or {}).get("source_commit", "unknown") == "unknown"]
    auth_unknown = [p for p in unknown if (manifest.get(p) or {}).get("auth_relevant")]
    print(f"artifacts: {len(found)} committed, {len(problems)} problem(s), {len(unknown)} without source commit "
          f"({len(auth_unknown)} auth-relevant)")
    for p in problems:
        print("  " + p)
    for p in auth_unknown:
        print("  provenance unknown (auth-relevant): " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
