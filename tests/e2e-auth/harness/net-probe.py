#!/usr/bin/env python3
"""Network segmentation probe (backlog KC-43-01, KC-43-03, KC-07-05, KC-07-10) against the REAL compose network layout.

  python3 -I tests/e2e-auth/harness/net-probe.py [--keep]

Reads the merged docker-compose.yml (every profile) and starts one small stand-in container per service in project
nidan-netprobe: same networks (internal flags kept, fixed subnets/addresses dropped), same hostnames (service name,
container_name, aliases), listening on the ports the real service listens on. Then it opens TCP connections from inside
the stand-ins and compares with the rules below. The apps themselves are not started: this tests the network design,
which is what decides who can reach whom. Removes the project at the end (containers + networks, never volumes;
--keep leaves it running for inspection). Exit 1 when any rule is broken.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROJECT = "nidan-netprobe"
IMAGE = "python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4"

# what the real services listen on, inside the network
LISTEN = {"keycloak": [8080, 9000], "keycloak-admin-proxy": [8180], "keycloak-db": [5432], "oauth2-proxy": [4180], "nidan-kafka": [9092],
          "openmrs-db": [3306], "openelis-db": [5432], "odoo-db": [5432], "orthanc-db": [5432],
          "nidan-integration-db": [5432], "nidan-dhis2-db": [3306], "openmrs-backend": [8080], "odoo": [8069],
          "openelis": [8080], "gateway": [80, 8099]}

APPS = ["odoo", "openmrs-backend", "openelis", "superset", "nidan-dhis2", "nidan-cis", "nidan-ois"]
AUTH_SIDE = ["gateway", "oauth2-proxy", "keycloak"]
# (from, host, port, must_connect, rule)
RULES = ([(a, h, p, False, "KC-43-01/KC-07-05") for a in APPS
          for h, p in (("keycloak", 8080), ("keycloak", 9000), ("keycloak-db", 5432), ("oauth2-proxy", 4180),
                       ("keycloak-admin-proxy", 8180))]
         + [(s, h, p, False, "KC-43-03") for s in AUTH_SIDE
            for h, p in (("nidan-kafka", 9092), ("openmrs-db", 3306), ("openelis-db", 5432), ("odoo-db", 5432))]
         # KC-07-10: Keycloak and its database reach no app database
         + [(s, h, p, False, "KC-07-10") for s in ("keycloak", "keycloak-db")
            for h, p in (("openmrs-db", 3306), ("odoo-db", 5432), ("openelis-db", 5432), ("orthanc-db", 5432),
                         ("nidan-integration-db", 5432), ("nidan-dhis2-db", 3306))]
         # the probe itself works: these paths must stay open
         + [("gateway", "keycloak", 8080, True, "control"), ("gateway", "openmrs-backend", 8080, True, "control"),
            ("openmrs-backend", "openmrs-db", 3306, True, "control"), ("nidan-cis", "nidan-kafka", 9092, True, "control"),
            ("odoo", "odoo-db", 5432, True, "control"), ("oauth2-proxy", "keycloak", 8080, True, "control"),
            ("keycloak-admin-proxy", "keycloak", 8080, True, "control"),
            # every service that uses a database still reaches it (data-net)
            ("superset", "openmrs-db", 3306, True, "control"), ("nidan-dhis2", "odoo-db", 5432, True, "control"),
            ("nidan-cis", "openelis-db", 5432, True, "control"), ("nidan-ois", "nidan-integration-db", 5432, True, "control"),
            ("orthanc", "orthanc-db", 5432, True, "control"), ("openelis", "openelis-db", 5432, True, "control"),
            ("nidan-dhis2", "nidan-dhis2-db", 3306, True, "control")]
         # the browser-facing frontends have no business with a database
         + [(f, h, p, False, "KC-43-03") for f in ("openmrs-frontend", "openelis-frontend")
            for h, p in (("openmrs-db", 3306), ("openelis-db", 5432), ("odoo-db", 5432))])

LISTENER = ("import socket, sys, threading\n"
            "def serve(p):\n"
            "    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('0.0.0.0', p))\n"
            "    s.listen(16)\n"
            "    while True: s.accept()[0].close()\n"
            "for p in sys.argv[1:]: threading.Thread(target=serve, args=(int(p),), daemon=True).start()\n"
            "threading.Event().wait()\n")

CONNECT = ("import socket, sys\n"
           "try: socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=2).close(); print('open')\n"
           "except OSError: print('closed')\n")


def merged():
    out = subprocess.run(["docker", "compose", "--profile", "*", "config", "--format", "json"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def stand_in_compose(cfg):
    nets = {n: {"internal": bool(v.get("internal"))} for n, v in cfg["networks"].items()}
    services = {}
    for name, s in cfg["services"].items():
        aliases = sorted({name, s.get("container_name") or name})
        mine = {}
        for n, v in (s.get("networks") or {}).items():
            mine[n] = {"aliases": sorted(set(aliases) | set((v or {}).get("aliases") or []))}
        ports = [str(p) for p in LISTEN.get(name, [])]
        services[name] = {"image": IMAGE, "networks": mine, "command": ["python3", "-c", LISTENER] + ports,
                          "stop_grace_period": "1s"}
    return {"name": PROJECT, "services": services, "networks": nets}


def run(compose_file, *args, check=True):
    return subprocess.run(["docker", "compose", "-p", PROJECT, "-f", str(compose_file), *args], capture_output=True,
                          text=True, check=check)


def probe(compose_file, src, host, port):
    r = run(compose_file, "exec", "-T", src, "python3", "-c", CONNECT, host, str(port), check=False)
    return r.stdout.strip() or f"error: {r.stderr.strip()[:120]}"


def main(argv):
    keep = "--keep" in argv
    cfg = merged()
    missing = sorted({x for r in RULES for x in (r[0], r[1])} - set(cfg["services"]))
    if missing:
        print(f"services not in the merged compose: {missing}")
        return 2
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "netprobe.json"
        f.write_text(json.dumps(stand_in_compose(cfg)))
        bad = 0
        try:
            run(f, "up", "-d", "--wait")
            for src, host, port, must, rule in RULES:
                got = probe(f, src, host, port)
                ok = got == ("open" if must else "closed")
                bad += not ok
                print(f"{'ok  ' if ok else 'FAIL'} {rule:18s} {src:16s} -> {host}:{port:<5d} {got}"
                      + ("" if ok else f"   (must be {'open' if must else 'closed'})"))
        finally:
            if not keep:
                run(f, "down", "--timeout", "1", check=False)   # containers + networks of nidan-netprobe; no volumes
    print(f"{bad} rule(s) broken" if bad else "segmentation as specified")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
