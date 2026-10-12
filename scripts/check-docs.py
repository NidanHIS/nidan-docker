#!/usr/bin/env python3
"""Every compose variable and every script is documented (backlog KC-29-01).

  scripts/check-docs.py

Fails when a ${VAR} used in docker-compose.yml / keycloak.docker-compose.yml / dev.docker-compose.yml is not in env.template
(a commented "# VAR=default" line counts), or a scripts/*.py|*.sh (not test_*) is not named in
README.md.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Spec section 3 switches (KC-01-04), by their implemented names: GATEWAY_HOST became KEYCLOAK_PUBLIC_URL +
# GATEWAY_CERT_SANS; DICOM_MODALITY_CIDR and the SSO overrides are read by scripts, not only by compose.
SPEC_VARS = ("NIDAN_SSO_ENABLED", "KEYCLOAK_PUBLIC_URL", "GATEWAY_CERT_SANS", "OPENMRS_SSO_ENABLED",
             "OPENELIS_SSO_ENABLED", "SUPERSET_SSO_ENABLED", "DHIS2_AUTH_PROVIDER", "OPENMRS_AUTH_MODE",
             "DICOM_BIND_IP", "DICOM_MODALITY_CIDR", "SUPPORT_PHONE", "DEFAULT_LOCALE")


def problems(root=ROOT):
    used = set()
    for f in ("docker-compose.yml", "keycloak.docker-compose.yml", "dev.docker-compose.yml"):
        if (root / f).exists():
            used |= set(re.findall(r"(?<!\$)\$\{([A-Z][A-Z0-9_]*)", (root / f).read_text(encoding="utf-8")))  # $${X} = container-side
    template = (root / "env.template").read_text(encoding="utf-8")
    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", template, re.M))
    readme = (root / "README.md").read_text(encoding="utf-8")
    out = [f"{v}: used in compose, missing from env.template" for v in sorted(used - documented)]
    out += [f"{v}: spec section 3 switch missing from env.template" for v in SPEC_VARS if v not in documented]
    for p in sorted((root / "scripts").iterdir()):
        if p.is_file() and p.suffix in (".py", ".sh") and not p.name.startswith("test_") and p.name not in readme:
            out.append(f"scripts/{p.name}: not described in README.md (## Scripts)")
    return out


def main():
    found = problems()
    print(f"docs: {len(found)} problem(s)")
    for p in found:
        print("  " + p)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
