#!/usr/bin/env python3
"""Static checks on the Keycloak realm files (backlog KC-54-01, KC-01-03 static part).

  scripts/check-realm.py [realm.yaml ...]      (default: keycloak/realm/**/*.yaml)

For every client:
  * redirect URIs and post-logout URIs are exact: no '*', no '+', https only
  * webOrigins has no '*' or '+'
  * implicitFlowEnabled and directAccessGrantsEnabled are false (admin-cli included)
  * fullScopeAllowed is false (explicit scope mappings only)
  * code-flow clients use PKCE S256 (openmrs is the documented exception: no PKCE in oauth2login)
  * token exchange is off
  * only the machine clients may carry an openmrs_user mapper, and those have no browser flow
Realm-level: no self-registration, no reset-password, no username edits, no permanent lockout,
no offline_access default role.
Exit 0 = clean, 1 = violations (printed).
"""
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
# master-realm automation clients (KC-07-07): service-account only, like the machine clients
AUTOMATION = {"nidan-config-cli", "nidan-incident", "nidan-role-sync"}
MACHINE = {"cis", "odoo-sync", "portal-sync", "role-sync"}
NO_PKCE_OK = {"openmrs"}
BUILT_IN = {"admin-cli"}


def check_profile(doc, where):
    """KC-55-01/03: declarative profile present on the realm, username pattern from env, edits by
    admin only, no unmanaged attributes."""
    errs = []
    comps = ((doc.get("components") or {}).get("org.keycloak.userprofile.UserProfileProvider") or [])
    if not comps:
        return [f"{where}: realm has no declarative user profile"] if doc.get("realm") == "nidan" else []
    try:
        cfg = json.loads(comps[0]["config"]["kc.user.profile.config"][0])
    except (KeyError, IndexError, TypeError, ValueError) as e:
        return [f"{where}: user profile config unreadable ({e})"]
    if cfg.get("unmanagedAttributePolicy"):
        errs.append(f"{where}: unmanagedAttributePolicy must be unset (unmanaged attributes disabled)")
    attrs = {a.get("name"): a for a in cfg.get("attributes") or []}
    pat = ((attrs.get("username") or {}).get("validations") or {}).get("pattern") or {}
    if pat.get("pattern") != "$(env:KC_USERNAME_PATTERN)":
        errs.append(f"{where}: username must be validated by $(env:KC_USERNAME_PATTERN) (reserved names)")
    for name, a in attrs.items():
        if (a.get("permissions") or {}).get("edit") != ["admin"]:
            errs.append(f"{where}: attribute {name} must be editable by admin only")
    return errs


def check(doc, where):
    errs = check_profile(doc, where)
    for key, bad in (("registrationAllowed", True), ("resetPasswordAllowed", True),
                     ("editUsernameAllowed", True), ("permanentLockout", True)):
        # The nidan realm states each one: keycloak-config-cli leaves unlisted properties as they are, so a
        # flag switched on in the admin console would survive every re-import.
        if doc.get(key) is bad or (doc.get("realm") == "nidan" and doc.get(key) is not (not bad)):
            errs.append(f"{where}: realm {key} must be {not bad}")
    composites = (doc.get("defaultRole") or {}).get("composites") or {}
    if "offline_access" in str(composites):
        errs.append(f"{where}: offline_access must not be a default role")
    if doc.get("realm") == "nidan":
        # Keycloak seeds the default role with manage-account, offline_access, uma_authorization; only an explicit
        # composite list (managed as a realm role by keycloak-config-cli) removes them.
        dr = next((r for r in ((doc.get("roles") or {}).get("realm") or []) if r.get("name") == "default-roles-nidan"), None)
        if not dr or dr.get("composites") != {"client": {"account": ["view-profile"]}}:
            errs.append(f"{where}: roles.realm must define default-roles-nidan with composites exactly "
                        "{client: {account: [view-profile]}} (no manage-account, no offline_access)")
    for c in doc.get("clients") or []:
        cid = c.get("clientId", "?")
        p = f"{where}: client {cid}"
        if c.get("implicitFlowEnabled") is not False and cid not in BUILT_IN:
            errs.append(f"{p}: implicitFlowEnabled must be false")
        if c.get("directAccessGrantsEnabled") is not False:
            errs.append(f"{p}: directAccessGrantsEnabled must be false")
        if cid in BUILT_IN:
            continue
        if c.get("fullScopeAllowed") is not False:
            errs.append(f"{p}: fullScopeAllowed must be false")
        attrs = c.get("attributes") or {}
        if str(attrs.get("standard.token.exchange.enabled", "false")).lower() != "false":
            errs.append(f"{p}: token exchange must be off")
        uris = list(c.get("redirectUris") or [])
        uris += [u for u in str(attrs.get("post.logout.redirect.uris", "")).split("##") if u]
        for u in uris:
            if "*" in u or u == "+" or not u.startswith("https://"):
                errs.append(f"{p}: redirect/logout URI must be exact https: {u}")
        for o in c.get("webOrigins") or []:
            if "*" in o or o == "+":
                errs.append(f"{p}: webOrigins must not use * or +")
        if c.get("standardFlowEnabled") and cid not in NO_PKCE_OK and attrs.get("pkce.code.challenge.method") != "S256":
            errs.append(f"{p}: code flow requires PKCE S256")
        emits = any((m.get("config") or {}).get("claim.name") == "openmrs_user" for m in c.get("protocolMappers") or [])
        if emits and cid not in MACHINE:
            errs.append(f"{p}: only {sorted(MACHINE)} may emit openmrs_user")
        if doc.get("realm") == "master" and cid not in AUTOMATION:
            errs.append(f"{p}: only {sorted(AUTOMATION)} may be defined in the master realm")
        if cid in MACHINE | AUTOMATION and (c.get("standardFlowEnabled") or not c.get("serviceAccountsEnabled")):
            errs.append(f"{p}: machine client must be service-account only (no browser flow)")
        if c.get("publicClient") and cid != "account-console":
            errs.append(f"{p}: must be confidential")
    return errs


def config_cli_env(compose=ROOT / "keycloak.docker-compose.yml"):
    svc = (yaml.safe_load(compose.read_text()).get("services") or {}).get("keycloak-config-cli") or {}
    env = svc.get("environment") or {}
    return set(env) if isinstance(env, dict) else {e.split("=", 1)[0] for e in env}


def substitution_problems(text, where, env):
    """keycloak-config-cli substitutes $(env:X) everywhere, comments included: an unknown X fails the import."""
    errs = []
    for n, line in enumerate(text.splitlines(), 1):
        code, _, comment = line.partition(" #") if not line.lstrip().startswith("#") else ("", "", line)
        if "$(" in comment:
            errs.append(f"{where}:{n}: '$(' in a comment is substituted too; reword it")
        for var in re.findall(r"\$\(env:([A-Za-z0-9_]+)", code):
            if var not in env:
                errs.append(f"{where}:{n}: $(env:{var}) is not passed to keycloak-config-cli in keycloak.docker-compose.yml")
    return errs


def main():
    files = [Path(a) for a in sys.argv[1:]] or sorted((ROOT / "keycloak" / "realm").rglob("*.yaml"))
    errs = []
    env = config_cli_env()
    for f in files:
        name = f.relative_to(ROOT) if f.is_absolute() and f.is_relative_to(ROOT) else f
        errs += check(yaml.safe_load(f.read_text()) or {}, str(name))
        if "roles" not in f.parts:   # role catalogs are not imported
            errs += substitution_problems(f.read_text(), str(name), env)
    print(f"{len(files)} realm file(s), {len(errs)} violation(s)")
    for e in errs:
        print("  " + e)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
