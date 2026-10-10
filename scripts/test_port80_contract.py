#!/usr/bin/env python3
"""KC-47-02: proxy/nginx.conf has exactly one server listening on :80, and it only serves ACME files and redirects to
https (plus the host check and path-trick guard every server has). Run: python3 -I scripts/test_port80_contract.py"""
import re
from pathlib import Path

CONF = (Path(__file__).resolve().parent.parent / "proxy" / "nginx.conf").read_text(encoding="utf-8")


def servers(text):
    """Bodies of every `server { ... }` block (brace-matched, comments stripped)."""
    text = re.sub(r"#[^\n]*", "", text)
    out, i = [], 0
    while (m := re.compile(r"\bserver\s*\{").search(text, i)):
        depth, j = 0, m.end() - 1
        while True:
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            if depth == 0:
                break
            j += 1
        out.append(text[m.end():j])
        i = j
    return out


port80 = [s for s in servers(CONF) if re.search(r"\blisten\s+80\b[\s;]", s)]
assert len(port80) == 1, f"{len(port80)} servers listen on :80"
body = port80[0]
locations = re.findall(r"\blocation\s+([^{]+?)\s*\{([^}]*)\}", body)
assert {loc.strip() for loc, _ in locations} == {"/.well-known/acme-challenge/", "/"}, locations
for loc, inner in locations:
    if loc.strip() == "/":
        assert re.fullmatch(r"\s*return 301 https://\$host\$request_uri;\s*", inner), inner
    else:
        assert "proxy_pass" not in inner and "root /var/www/certbot;" in inner, inner
assert "proxy_pass" not in body
print("port-80 contract: OK")
