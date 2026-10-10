#!/usr/bin/env python3
"""Compare the gateway's nginx.conf with the route contract proxy/routes.yaml (backlog KC-01-02/06).

  scripts/check-routes.py [path/to/nginx.conf]      (default: proxy/nginx.conf)

Reports, per server (mapped by listen port):
  * MISSING   a contract route with no matching nginx location
  * EXTRA     an nginx location the contract does not list (an untracked route)
  * UNGATED   a location the contract gates (gate = a role) with no effective auth_request
              (own directive or inherited from the server; `auth_request off` disables it)
  * OPEN      a location the contract marks deny that still proxies somewhere
  * WRONGGATE a gated location whose auth_request checks a different role than the contract
  * RETURN    a gated location that answers with a non-3xx/4xx `return` (that skips auth_request, rule 1)
Includes of /etc/nginx/auth/*.conf are read from proxy/auth/ in their SSO-on form.
Exit 0 = nginx matches the contract, 1 = differences (expected until KC-10 is built).
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PORT_SERVER = {"80": "http", "443": "main", "8443": "oe-api", "8069": "odoo", "9443": "staff", "8099": "backchannel"}
MATCH = {"=": "exact", "^~": "prefix-priority", "~": "regex", "~*": "regex", "": "prefix"}


def strip_comments(text):
    return "\n".join(re.sub(r"(^|\s)#.*$", r"\1", line) for line in text.splitlines())


def inline_auth(text, auth_dir=ROOT / "proxy" / "auth"):
    """Replace `include /etc/nginx/auth/X;` with proxy/auth/X (SSO on), recursively."""
    def sub(m):
        f = auth_dir / Path(m.group(1)).name
        return inline_auth(f.read_text(), auth_dir) if f.exists() else ""
    return re.sub(r"include\s+/etc/nginx/auth/(\S+?);", sub, text)


def parse_nginx(text):
    """Yield (server_name, match, path, gated, proxies) for every location. Crude but sufficient:
    nginx.conf here has no nested includes and no braces inside strings."""
    tokens = re.findall(r"\{|\}|[^{};]+;|[^{};]+(?=\{)", strip_comments(text))
    stack = []  # frames: dict(kind, ports, auth)
    current = None
    for tok in tokens:
        tok = tok.strip()
        if tok.endswith("{") or tok == "{":
            continue
        if tok == "}":
            frame = stack.pop()
            if frame["kind"] == "location":
                yield frame
            continue
        if tok.endswith(";"):
            stmt = tok[:-1].strip()
            if not stack:
                continue
            frame = stack[-1]
            m = re.match(r"listen\s+(\S+)", stmt)
            if m and frame["kind"] == "server":
                frame["ports"].append(m.group(1).split(":")[-1])
            if stmt.startswith("auth_request "):
                target = stmt.split(None, 1)[1].strip()
                # only a role check is a login gate; other auth_request targets (e.g. the status-only health
                # probe /_ping_orthanc) must not make a route count as gated
                frame["auth"] = target.startswith("/_auth_")
                frame["role"] = target[len("/_auth_"):] if target.startswith("/_auth_") else None
            m2 = re.match(r"return\s+(\d{3})", stmt)
            if m2 and frame["kind"] == "location":
                frame.setdefault("returns", []).append(m2.group(1))
            if stmt == "internal" and frame["kind"] == "location":
                frame["internal"] = True
            if re.match(r"(proxy_pass|fastcgi_pass|uwsgi_pass|grpc_pass)\s", stmt):
                frame["proxies"] = True
            continue
        # block opener (the text before "{")
        head = tok
        if re.fullmatch(r"server", head):
            stack.append({"kind": "server", "ports": [], "auth": False})
        elif head.startswith("location"):
            m = re.match(r"location\s+(=|\^~|~\*|~)?\s*(.+)", head)
            server = next((f for f in reversed(stack) if f["kind"] == "server"), None)
            stack.append({"kind": "location", "match": MATCH[m.group(1) or ""], "path": m.group(2).strip().strip('"'),
                          "server": server, "auth": None, "proxies": False})
        else:
            stack.append({"kind": head.split()[0] if head else "block", "ports": [], "auth": None})


def main():
    conf = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "proxy" / "nginx.conf"
    contract = yaml.safe_load((ROOT / "proxy" / "routes.yaml").read_text())["routes"]
    want = {(r["server"], r["match"], r["path"]): r for r in contract}
    have = {}
    for loc in parse_nginx(inline_auth(conf.read_text())):
        if loc.get("internal") or loc["path"].startswith("@"):
            continue  # internal locations (auth/probe subrequest targets) and named handlers: not reachable by a client
        ports = loc["server"]["ports"] if loc["server"] else []
        names = sorted({PORT_SERVER.get(p, f"port-{p}") for p in ports}) or ["?"]
        gated = loc["auth"] if loc["auth"] is not None else loc["server"]["auth"]
        for name in names:
            key = (name, loc["match"], loc["path"])
            # the same listen port may appear in two server blocks (e.g. two :80 servers): keep both
            have.setdefault(key, []).append({"gated": gated, "proxies": loc["proxies"], "role": loc.get("role"),
                                             "returns": loc.get("returns", [])})
    problems = []
    for key, r in want.items():
        locs = have.get(key)
        if not locs:
            problems.append(f"MISSING  {key[0]:7s} {key[1]:15s} {key[2]}")
            continue
        if r["gate"] not in ("exempt", "deny") and not all(l["gated"] for l in locs):
            problems.append(f"UNGATED  {key[0]:7s} {key[1]:15s} {key[2]}  (contract gate: {r['gate']})")
        if r["gate"] not in ("exempt", "deny"):
            for l in locs:
                if l["gated"] and l["role"] and l["role"] != r["gate"]:
                    problems.append(f"WRONGGATE {key[0]:7s} {key[1]:15s} {key[2]}  (checks {l['role']}, contract {r['gate']})")
                if any(not c.startswith(("3", "4")) for c in l["returns"]):
                    problems.append(f"RETURN   {key[0]:7s} {key[1]:15s} {key[2]}  (return {l['returns']} skips auth_request)")
        if r["gate"] == "deny" and any(l["proxies"] for l in locs):
            problems.append(f"OPEN     {key[0]:7s} {key[1]:15s} {key[2]}  (contract: deny)")
    for key in sorted(have):
        if key not in want:
            problems.append(f"EXTRA    {key[0]:7s} {key[1]:15s} {key[2]}")
    print(f"{len(want)} contract routes, {sum(len(v) for v in have.values())} nginx locations, {len(problems)} differences")
    for p in problems:
        print("  " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
