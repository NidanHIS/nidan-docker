#!/usr/bin/env python3
"""Public hostnames reach only the gateway, over TLS (backlog KC-44-01).

  scripts/check-ingress.py

Reads every compose file in this directory (docker-compose*.yml, dev.docker-compose.yml, and the Coolify
export docker-compose-resolved) and finds services an edge proxy routes a hostname to: Traefik router
labels with Host()/HostSNI(), caddy_N labels (caddy-docker-proxy), VIRTUAL_HOST (nginx-proxy).
  * only `gateway` may be routed: anything else skips central login and the gateway's rules
  * the gateway only on its TLS ports (443, 8443): Traefik HTTP needs loadbalancer.server.scheme=https,
    Caddy needs an https upstream
Findings listed in security/ingress.pending (file service) are reported, not failed: they await an
approved change to that deployment. Fixed entries must be removed (stale ones fail).
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PENDING = ROOT / "security" / "ingress.pending"
TLS_PORTS = {"443", "8443"}


def compose_files(root=ROOT):
    return sorted({*root.glob("docker-compose*.yml"), *root.glob("dev.docker-compose.yml"),
                   *root.glob("docker-compose-resolved")})


def labels_of(svc):
    labels = svc.get("labels") or []
    if isinstance(labels, dict):
        return {str(k): str(v) for k, v in labels.items()}
    return dict(str(l).split("=", 1) if "=" in str(l) else (str(l), "") for l in labels)


def env_of(svc):
    env = svc.get("environment") or {}
    if isinstance(env, list):
        env = dict(str(e).split("=", 1) if "=" in str(e) else (str(e), "") for e in env)
    return env


def service_problems(name, svc):
    labels, out = labels_of(svc), []
    traefik = any(k.startswith("traefik.") and k.endswith(".rule") and re.search(r"Host(SNI)?\(", v)
                  for k, v in labels.items())
    caddy = [k for k in labels if re.fullmatch(r"caddy(_\d+)?", k)]
    vhost = bool(env_of(svc).get("VIRTUAL_HOST"))
    if not (traefik or caddy or vhost):
        return []
    if name != "gateway":
        return [f"{name}: a public hostname is routed to it directly (only gateway may be)"]
    if traefik:
        for k, port in labels.items():
            if not k.endswith(".loadbalancer.server.port"):
                continue
            scheme = labels.get(k[: -len("port")] + "scheme", "")
            if str(port) not in TLS_PORTS or (".http." in k and scheme != "https"):
                out.append(f"gateway: Traefik forwards to port {port} over {scheme or 'http'} (needs https on 443/8443)")
    for c in caddy:
        ups = " ".join(v for k, v in labels.items() if k.startswith(c + ".") and "reverse_proxy" in k)
        if not re.search(r"https", ups) or not re.search(r"\b(443|8443)\b", ups):
            out.append(f"gateway: Caddy {c} forwards to {ups or '?'} (needs an https upstream on 443/8443)")
    if vhost and str(env_of(svc).get("VIRTUAL_PROTO", "http")) != "https":
        out.append("gateway: nginx-proxy VIRTUAL_PROTO is not https")
    return sorted(set(out))


def findings(root=ROOT):
    out = []
    for f in compose_files(root):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for name, svc in sorted((doc.get("services") or {}).items()):
            out += [(f.name, name, p) for p in service_problems(name, svc or {})]
    return out


def main(root=ROOT, pending_file=PENDING):
    pending = set()
    if pending_file.exists():
        pending = {tuple(l.split()[:2]) for l in pending_file.read_text().splitlines()
                   if l.strip() and not l.startswith("#")}
    found = findings(root)
    keys = {(f, s) for f, s, _ in found}
    for f, s, p in found:
        print(("  PENDING " if (f, s) in pending else "  FAIL    ") + f"{f}: {p}")
    stale = sorted(pending - keys)
    for f, s in stale:
        print(f"  STALE   {f} {s}: fixed, remove it from security/ingress.pending")
    new = [k for k in keys if k not in pending]
    print(f"ingress: {len(found)} finding(s), {len(new)} not pending, {len(stale)} stale")
    return 1 if new or stale else 0


if __name__ == "__main__":
    sys.exit(main())
