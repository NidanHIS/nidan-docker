"""Self-test for check-ingress.py: `python3 -I scripts/test_check_ingress.py`."""
import importlib.util
import tempfile
from pathlib import Path

import yaml

spec = importlib.util.spec_from_file_location("ci", Path(__file__).resolve().parent / "check-ingress.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)

ok_traefik = {"labels": ["traefik.http.routers.r.rule=Host(`h.example`)",
                         "traefik.http.services.r.loadbalancer.server.port=443",
                         "traefik.http.services.r.loadbalancer.server.scheme=https"]}
assert ci.service_problems("gateway", ok_traefik) == []
assert ci.service_problems("gateway", {"labels": {"traefik.tcp.routers.t.rule": "HostSNI(`h.example`)",
                                                   "traefik.tcp.services.t.loadbalancer.server.port": "8443"}}) == []
assert ci.service_problems("gateway", {"labels": ["caddy_0=https://h.example",
                                                   "caddy_0.reverse_proxy=https://{{upstreams 443}}"]}) == []
assert ci.service_problems("odoo", {"labels": ["traefik.enable=false"]}) == []  # not routed
plain = {"labels": ["traefik.http.routers.r.rule=Host(`h.example`)", "traefik.http.services.r.loadbalancer.server.port=80"]}
assert "port 80" in ci.service_problems("gateway", plain)[0]
assert "routed to it directly" in ci.service_problems("odoo", plain)[0]
assert ci.service_problems("superset", {"environment": ["VIRTUAL_HOST=bi.example"]})
assert "https upstream" in ci.service_problems("gateway", {"labels": ["caddy_0=h.example", "caddy_0.reverse_proxy={{upstreams 80}}"]})[0]

with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    (root / "docker-compose.yml").write_text(yaml.safe_dump({"services": {"gateway": ok_traefik, "odoo": plain}}))
    pending = root / "ingress.pending"
    assert ci.main(root, pending) == 1                       # new finding fails
    pending.write_text("docker-compose.yml odoo  reason\n")
    assert ci.main(root, pending) == 0                       # pending is reported only
    pending.write_text("docker-compose.yml odoo  reason\ndocker-compose.yml gateway  fixed already\n")
    assert ci.main(root, pending) == 1                       # stale entry fails
assert ci.main() == 0
print("check-ingress: OK")
