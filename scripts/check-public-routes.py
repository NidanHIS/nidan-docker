#!/usr/bin/env python3
"""Fail when any unauthenticated route is not on the reviewed allow-list (backlog KC-53-05).

Finds:
  * Odoo  @http.route(..., auth="public"|"none")   in the Odoo addon repos
  * Spring .permitAll() matchers                   in CIS, OIS and OpenELIS security configs
and compares them with security/public-routes.allow. Every allow-list entry names the check that
protects the route; an entry whose check is NONE is a known hole and fails the run too.

Usage: scripts/check-public-routes.py [MONOREPO_ROOT]   (default: the parent of nidan-docker)
Exit 0 = every public route is listed with a real check; 1 = something to fix.
"""
import ast
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALLOW_FILE = HERE.parent / "security" / "public-routes.allow"

ODOO_REPOS = ["odoo_19_addons", "nidan_odoo_extra_addons", "odoo_payment_gateway"]
SPRING_FILES = {
    "cis": "nidan-middleware/integration/cis/src/main/java/org/nidan/integration/cis/config/SecurityConfig.java",
    "ois": "nidan-middleware/integration/ois/src/main/java/org/nidan/integration/ois/config/SecurityConfig.java",
    "openelis": "OpenELIS-Global-2/src/main/java/org/openelisglobal/security/SecurityConfig.java",
}
CHECKS = {"shared-secret", "secret-or-admin", "payment-token", "static-asset", "login-page", "health",
          "error-page", "NONE", "signed-token"}


def odoo_public_routes(root):
    for repo in ODOO_REPOS:
        base = root / repo
        if not base.is_dir():
            continue
        for py in sorted(base.rglob("*.py")):
            if "/tests/" in py.as_posix():
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for dec in node.decorator_list:
                    if not (isinstance(dec, ast.Call) and getattr(dec.func, "attr", "") == "route"):
                        continue
                    auth = next((k.value for k in dec.keywords if k.arg == "auth"), None)
                    if not (isinstance(auth, ast.Constant) and auth.value in ("public", "none")):
                        continue
                    paths = dec.args[0] if dec.args else next((k.value for k in dec.keywords if k.arg == "route"), None)
                    if isinstance(paths, ast.Constant):
                        paths = [paths.value]
                    elif isinstance(paths, ast.List):
                        paths = [e.value for e in paths.elts if isinstance(e, ast.Constant)]
                    else:
                        paths = ["<dynamic>"]
                    for p in paths:
                        yield "odoo", p, f"{py.relative_to(root)}:{dec.lineno}"


def _strings(expr, arrays):
    out = re.findall(r'"([^"]+)"', expr)
    for ident in re.findall(r"\b([A-Z][A-Z0-9_]+)\b", expr):
        out += arrays.get(ident, [])
    return out


def spring_public_routes(root):
    for comp, rel in SPRING_FILES.items():
        f = root / rel
        if not f.is_file():
            continue
        src = f.read_text(encoding="utf-8")
        arrays = {m.group(1): re.findall(r'"([^"]+)"', m.group(2))
                  for m in re.finditer(r"String\[\]\s+([A-Z][A-Z0-9_]+)\s*=\s*\{(.*?)\};", src, re.S)}
        line = lambda pos: src.count("\n", 0, pos) + 1
        for m in re.finditer(r"requestMatchers\(([^)]*)\)\s*\.permitAll\(\)", src):
            for p in _strings(m.group(1), arrays):
                yield comp, p, f"{rel}:{line(m.start())}"
        for m in re.finditer(r"anyRequest\(\)\s*\.permitAll\(\)", src):
            # scope of an anyRequest().permitAll(): the securityMatcher of the same chain, else everything
            head = src[:m.start()]
            chain = head[max(head.rfind("SecurityFilterChain"), head.rfind("http.")):]
            sm = re.search(r"securityMatcher\(([^)]*)\)", chain)
            for p in (_strings(sm.group(1), arrays) if sm else ["/**"]):
                yield comp, p, f"{rel}:{line(m.start())}"


def load_allow():
    entries = {}
    for n, raw in enumerate(ALLOW_FILE.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 4 or parts[2] not in CHECKS:
            sys.exit(f"{ALLOW_FILE.name}:{n}: expected 'component | path | check | reason' with check in {sorted(CHECKS)}")
        entries[(parts[0], parts[1])] = (parts[2], parts[3], n)
    return entries


def main():
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE.parent.parent
    allow = load_allow()
    found = list(odoo_public_routes(root)) + list(spring_public_routes(root))
    seen, problems = set(), []
    for comp, path, where in found:
        seen.add((comp, path))
        entry = allow.get((comp, path))
        if entry is None:
            problems.append(f"UNLISTED  {comp} {path}  ({where}) — add it with the check that protects it")
        elif entry[0] == "NONE":
            problems.append(f"NO CHECK  {comp} {path}  ({where}) — {entry[1]}")
    for (comp, path), (_, _, n) in sorted(allow.items(), key=lambda kv: kv[1][2]):
        if (comp, path) not in seen:
            problems.append(f"STALE     {comp} {path}  ({ALLOW_FILE.name}:{n}) — no longer in code; drop the entry")
    print(f"{len(found)} public routes found, {len(allow)} allow-list entries")
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
