#!/usr/bin/env python3
"""O3 log out ends the single sign-on session (backlog KC-35-03).

  scripts/check-esm-login.py [FRONTEND_DIR]       (default: openmrs/frontend next to this script)

Upstream esm-login does nothing after an oauth2 log out, so the gateway and Keycloak sessions survive and the next
person at the computer is signed straight back in as the previous user. Our esm-login tarball sends the browser to
provider.logoutUrl instead. This fails when the tarball the SPA is assembled from (spa-assemble-config.json, the
same file the Dockerfile copies and installs) lacks that patch in its source or its built bundle (e.g. after an
upgrade to a plain upstream esm-login), when the oauth2 path logs out over REST before leaving, or when the SSO SPA config no longer points log in/out at oauth2login.
"""
import json
import re
import sys
import tarfile
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent / "openmrs" / "frontend"


def problems(front):
    front, out = Path(front), []
    tgz = json.loads((front / "spa-assemble-config.json").read_text())["frontendModules"].get("@openmrs/esm-login-app", "")
    docker = (front / "Dockerfile").read_text()
    if not tgz.endswith(".tgz"):
        return [f"spa-assemble-config.json: @openmrs/esm-login-app is {tgz!r}, not our patched tarball"]
    for line in (f"COPY packages/{tgz} .", f"RUN yarn add ./{tgz}"):
        if line not in docker:
            out.append(f"Dockerfile does not `{line}`")
    with tarfile.open(front / "packages" / tgz) as tar:
        files = {m.name: m for m in tar.getmembers() if m.isfile()}
        read = lambda n: tar.extractfile(files[n]).read().decode("utf-8", "replace") if n in files else ""
        logout = read("package/src/redirect-logout/redirect-logout.component.tsx")
        if not re.search(r"window\.location\.assign\(\s*interpolateUrl\(\s*config\.provider\.logoutUrl", logout):
            out.append(f"{tgz}: redirect-logout does not send the browser to provider.logoutUrl")
        # ...and goes there before any REST logout: ending the OpenMRS session first let the SPA navigate to its own
        # login, which could cancel the logout navigation and leave the gateway session alive (2026-10-09).
        sso = logout.find("endSingleSignOn(config)) {")
        if sso < 0 or (0 <= logout.find("performLogout()") < sso):
            out.append(f"{tgz}: oauth2 log out must leave for logoutUrl before any REST logout (performLogout)")
        if "logoutUrl" not in read("package/src/config-schema.ts"):
            out.append(f"{tgz}: config schema has no provider.logoutUrl")
        if not any(n.startswith("package/dist/") and n.endswith(".js") and "logoutUrl" in read(n) for n in files):
            out.append(f"{tgz}: built bundle (dist/) lacks the logoutUrl patch; rebuild it from src/")
    sso = json.loads((front / "config-sso.json").read_text()).get("@openmrs/esm-login-app", {}).get("provider", {})
    if sso.get("type") != "oauth2" or not str(sso.get("logoutUrl", "")).endswith("/oauth2logout") \
            or not str(sso.get("loginUrl", "")).endswith("/oauth2login"):
        out.append(f"config-sso.json: provider must be oauth2 with loginUrl .../oauth2login and logoutUrl .../oauth2logout, is {sso}")
    if "config-sso.json" not in (front / "startup.sh").read_text() or "COPY config-sso.json" not in docker:
        out.append("config-sso.json is not shipped (Dockerfile COPY) and loaded when OPENMRS_SSO_ENABLED (startup.sh)")
    return out


def main(argv):
    out = problems(argv[1] if len(argv) > 1 else DEFAULT)
    for p in out:
        print("FAIL " + p)
    print("esm-login: oauth2 log out ends the SSO session" if not out else f"{len(out)} problem(s)")
    return 1 if out else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
