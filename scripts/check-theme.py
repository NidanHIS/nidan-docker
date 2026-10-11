#!/usr/bin/env python3
"""Keycloak theme safety (backlog KC-17-06, KC-18).

  scripts/check-theme.py

Fails when a theme template (*.ftl) disables escaping (?no_esc / ?noEscape / <#noautoesc>) outside
the audited list below, when a message file contains HTML, or when messages_en and messages_ne of
a theme do not have the same keys. Enumeration guard (KC-17-02): in every locale the disabled /
locked account messages must equal invalidUserMessage. A template copied from Keycloak must say which Keycloak
version it came from ("Keycloak X.Y.Z" in its first lines) and that must be the version compose runs, so an upgrade
forces a re-copy and review.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THEMES = ROOT / "keycloak" / "themes"
AUDITED_NO_ESC = set()  # "file.ftl:key" entries reviewed as safe; none today
SAME_AS_INVALID = ["accountDisabledMessage", "accountTemporarilyDisabledMessage", "accountPermanentlyDisabledMessage"]


def props(path):
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith(("#", "!")) and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def compose_keycloak_version(compose=ROOT / "docker-compose.yml"):
    m = re.search(r"image:\s*quay\.io/keycloak/keycloak:(\d+\.\d+\.\d+)", compose.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def problems(themes=THEMES, keycloak_version=None):
    out = []
    keycloak_version = keycloak_version or compose_keycloak_version()
    for ftl in sorted(themes.rglob("*.ftl")):
        head = "\n".join(ftl.read_text(encoding="utf-8").splitlines()[:5])
        copied = re.search(r"Keycloak (\d+\.\d+\.\d+)", head)
        if not copied:
            out.append(f"{ftl.relative_to(themes)}: does not say which Keycloak version it was copied from")
        elif keycloak_version and copied.group(1) != keycloak_version:
            out.append(f"{ftl.relative_to(themes)}: copied from Keycloak {copied.group(1)} but compose runs "
                       f"{keycloak_version}: re-copy the upstream file and re-apply the Nidan change")
        for n, line in enumerate(ftl.read_text(encoding="utf-8").splitlines(), 1):
            for m in re.finditer(r"\$\{([^}]*)\?(no_esc|noEscape)\}|<#noautoesc>", line):
                key = f"{ftl.name}:{(m.group(1) or 'noautoesc').strip()}"
                if key not in AUDITED_NO_ESC:
                    out.append(f"{ftl.relative_to(themes)}:{n}: unescaped output {m.group(0)!r} (not audited)")
    for msgdir in sorted({p.parent for p in themes.rglob("messages_*.properties")}):
        files = {p.stem.split("_", 1)[1]: props(p) for p in msgdir.glob("messages_*.properties")}
        for loc, d in files.items():
            for k, v in d.items():
                if re.search(r"<\s*/?\s*[a-zA-Z][^>]*>", v):
                    out.append(f"{msgdir.relative_to(themes)}/messages_{loc}: {k} contains HTML")
            if "invalidUserMessage" in d:
                for k in SAME_AS_INVALID:
                    if d.get(k, d["invalidUserMessage"]) != d["invalidUserMessage"]:
                        out.append(f"{msgdir.relative_to(themes)}/messages_{loc}: {k} differs from invalidUserMessage "
                                   "(reveals that the account exists)")
        if "en" in files and "ne" in files and set(files["en"]) != set(files["ne"]):
            out.append(f"{msgdir.relative_to(themes)}: en/ne keys differ: {sorted(set(files['en']) ^ set(files['ne']))}")
    return out


def main():
    found = problems()
    print(f"themes: {len(found)} problem(s)")
    for p in found:
        print("  " + p)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
