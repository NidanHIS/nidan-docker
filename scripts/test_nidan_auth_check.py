"""Self-test for nidan-auth-check.py: `python3 -I scripts/test_nidan_auth_check.py`."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("nac", Path(__file__).resolve().parent / "nidan-auth-check.py")
nac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nac)


def cfg(cis=None, odoo=None, gw=None, extra=None):
    services = {
        "gateway": {"environment": {"GATEWAY_DOMAIN": "ehr.lan", **(gw or {})},
                    "ports": [{"published": "443", "host_ip": ""}]},
        "nidan-cis": {"environment": {"OPENMRS_AUTH_MODE": "basic", **(cis or {})}},
        "odoo": {"environment": {"OPENMRS_AUTH_MODE": "basic", **(odoo or {})}},
        "openmrs-backend": {"environment": {"NIDANCORE_ODOO_URL": "http://odoo:8069"}},
    }
    services.update(extra or {})
    for s in services.values():
        s.setdefault("logging", {"options": {"max-size": "10m"}})
    return {"services": services}


def problems(c):
    return nac.check(c)[1]


jwt_ok = {"OPENMRS_AUTH_MODE": "jwt", "OPENMRS_AUTH_TOKEN_URL": "http://kc/token", "OPENMRS_AUTH_CLIENT_SECRET": "x"}

assert problems(cfg()) == [], problems(cfg())
# jwt while OpenMRS SSO is off
assert any("SSO is off" in p for p in problems(cfg(cis=jwt_ok, odoo=jwt_ok)))
# SSO on: both jwt with secrets is clean; a basic caller is a contradiction; mismatch flagged
on = {"NIDAN_SSO_ENABLED": "true"}
assert problems(cfg(cis={**jwt_ok, **on}, odoo=jwt_ok)) == []
assert any("still uses basic" in p for p in problems(cfg(cis={**jwt_ok, **on})))
assert any("disagree" in p for p in problems(cfg(cis={**jwt_ok, **on})))
# jwt without its secret
assert any("needs OPENMRS_AUTH_CLIENT_SECRET" in p
           for p in problems(cfg(cis={**jwt_ok, **on, "OPENMRS_AUTH_CLIENT_SECRET": ""}, odoo=jwt_ok)))
# paywall at the gateway
bad_paywall = {"openmrs-backend": {"environment": {"NIDANCORE_ODOO_URL": "https://ehr.lan:8069"}}}
assert any("paywall" in p.lower() or "NIDANCORE_ODOO_URL" in p for p in problems(cfg(extra=bad_paywall)))
oe = {"openelis": {"environment": {"CATALINA_OPTS": "-Dx=1 -Dorg.openelisglobal.nidan.paywall.odoo.url=https://localhost/odoo -Dy=2"}}}
assert any("paywall.odoo.url" in p for p in problems(cfg(extra=oe)))
# ports
assert any("3307" in p for p in problems(cfg(extra={"openmrs-db": {"ports": [{"published": "3307"}]}})))
# KC-07-04: the admin console port lives on keycloak-admin-proxy (auth-net + kc-admin-net), loopback only
proxy = {"networks": {"auth-net": {}, "kc-admin-net": {}}, "ports": [{"published": "8180", "host_ip": "127.0.0.1"}]}
assert problems(cfg(extra={"keycloak-admin-proxy": proxy})) == [], problems(cfg(extra={"keycloak-admin-proxy": proxy}))
wide = dict(proxy, ports=[{"published": "8180", "host_ip": "0.0.0.0"}])
assert any("8180" in p for p in problems(cfg(extra={"keycloak-admin-proxy": wide})))
# a port on a service that is only on internal networks is silently not served by Docker
c = cfg(extra={"keycloak": {"networks": {"auth-net": {}}, "ports": [{"published": "8180", "host_ip": "127.0.0.1"}]}})
c["networks"] = {"auth-net": {"internal": True}}
assert any("only on internal networks" in p for p in problems(c)), problems(c)
assert any("only keycloak-admin-proxy may be" in p for p in problems(cfg(extra={"odoo": {"networks": {"kc-admin-net": {}}}})))
# log cap
nolog = cfg()
nolog["services"]["odoo"]["logging"] = {}
assert any("odoo: no logging max-size" in p for p in problems(nolog))
print("nidan-auth-check self-test: OK")

# KC-12-06: only gateway / certbot mount TLS keys; orthanc is reported as pending, not failed
le = [{"type": "volume", "source": "certbot-etc", "target": "/etc/letsencrypt", "read_only": True}]
assert problems(cfg(gw={}, extra={"nidan-certbot": {"volumes": le}})) == []
assert any("KC-12-06" in p and "superset" in p for p in problems(cfg(extra={"superset": {"volumes": le}})))
assert any("KC-12-06" in p for p in problems(cfg(extra={"x": {"volumes": [{"type": "bind", "source": "/srv/keys/nginx.key", "target": "/k"}]}})))
c = cfg(extra={"orthanc": {"volumes": le}})
assert problems(c) == [] and any("pending approval to remove" in l for l in nac.check(c)[0])
# KC-01-08: gateway from ./proxy or its published image; nidan-proxy-image anywhere fails
c = cfg()
c["services"]["gateway"]["image"] = "trigonaltechnology/gateway:latest"
assert problems(c) == []
c["services"]["gateway"]["image"] = "nginx:1.27"
assert any("KC-01-08" in p for p in problems(c))
c = cfg()
c["services"]["gateway"]["build"] = {"context": "/repo/nidan-proxy-image"}
assert sum("KC-01-08" in p for p in problems(c)) == 2
c["services"]["gateway"]["build"] = {"context": "/repo/nidan-docker/proxy"}
assert problems(c) == []
# KC-43-04 static: auth-net membership
c = cfg(extra={"keycloak": {"networks": {"auth-net": {}}}, "keycloak-db": {"networks": {"auth-net": {}}}})
c["networks"] = {"auth-net": {"internal": True}}
assert problems(c) == [], problems(c)
c["services"]["odoo"]["networks"] = {"auth-net": {}, "nidan-network": {}}
c["services"]["keycloak"]["networks"] = {"auth-net": {}, "nidan-network": {}}
c["networks"]["auth-net"]["internal"] = False
out = problems(c)
assert any("odoo: on auth-net" in p for p in out) and any("keycloak: must be on auth-net only" in p for p in out), out
assert any("must be internal" in p for p in out), out
# KC-10-13 oauth2-proxy
real_cfg = (nac.ROOT / "proxy" / "oauth2-proxy.cfg").read_text()
o2 = {"image": "quay.io/oauth2-proxy/oauth2-proxy:v7.15.5@sha256:" + "a" * 64, "networks": {"auth-net": {}}}
assert nac.oauth2_proxy_problems(o2, real_cfg) == [], nac.oauth2_proxy_problems(o2, real_cfg)
bad = dict(o2, image="quay.io/oauth2-proxy/oauth2-proxy:v7.15.1", networks={"auth-net": {}, "nidan-network": {}},
           environment={"OAUTH2_PROXY_SKIP_AUTH_ROUTES": "^/api", "OAUTH2_PROXY_COOKIE_EXPIRE": "168h",
                        "OAUTH2_PROXY_SKIP_JWT_BEARER_TOKENS": "true", "OAUTH2_PROXY_CODE_CHALLENGE_METHOD": "plain"})
out = nac.oauth2_proxy_problems(bad, real_cfg)
for frag in ("older than 7.15.2", "skip_auth_routes", "skip_jwt_bearer_tokens", "exceeds 12h", "S256", "only on auth-net"):
    assert any(frag in p for p in out), (frag, out)
assert any("exceeds 12h" in p for p in nac.oauth2_proxy_problems(o2, real_cfg.replace('cookie_expire = "12h"', "")))
no_trust = "\n".join(l for l in real_cfg.splitlines() if not l.startswith("trusted_proxy_ips"))
assert any("trusted_proxy_ips" in p for p in nac.oauth2_proxy_problems(o2, no_trust))
assert any("trusted_proxy_ips" in p for p in nac.oauth2_proxy_problems(o2, real_cfg, "172.31.250.99"))
# KC-34-01: the authentication OMOD is found in the distro files (pom + properties)
import tempfile
with tempfile.TemporaryDirectory() as d:
    dist = Path(d) / "openmrs" / "distro"
    dist.mkdir(parents=True)
    (dist / "distro.properties").write_text("omod.webservices.rest=1\nomod.authentication=${v}\n")
    (dist / "pom.xml").write_text("<artifactId>authentication-omod</artifactId>\n")
    assert len(nac.openmrs_distro_lines(Path(d))) == 2
    (dist / "distro.properties").write_text("omod.oauth2login=1\n")
    (dist / "pom.xml").write_text("<artifactId>oauth2login-omod</artifactId>\n")
    assert nac.openmrs_distro_lines(Path(d)) == []
# OpenELIS form login must never be switched off (it removes the catch-all authenticated chain)
c = cfg(extra={"openelis": {"environment": {"CATALINA_OPTS": "-Dx=1 -Dorg.itech.login.form=false -Dy=2"}}})
assert any("org.itech.login.form" in p for p in problems(c)), problems(c)
assert problems(cfg(extra={"openelis": {"environment": {"CATALINA_OPTS": "-Dorg.itech.login.form=true"}}})) == []
# KC-45-01: no secret as a -D flag (Tomcat logs them); datasource.password reported only
c = cfg(extra={"openelis": {"environment": {"CATALINA_OPTS": "-Dds.url=x -Dorg.x.sync.secret=abc -Dapi.token=t -Doauth.tokenUri=http://kc/token -Dkey.path=/k"}}})
assert sum("in CATALINA_OPTS/JAVA_OPTS is logged" in p for p in problems(c)) == 2, problems(c)
c = cfg(extra={"openelis": {"environment": {"CATALINA_OPTS": "-Ddatasource.password=x -Dorg.x.enabled=true"}}})
assert problems(c) == [] and any("datasource.password" in l for l in nac.check(c)[0])
# KC-43-03: databases on data-net only; the gateway never on it; data-net internal
c = cfg(extra={"openmrs-db": {"networks": {"data-net": {}, "nidan-network": {}}}})
c.setdefault("networks", {})["data-net"] = {"internal": True}
assert any("openmrs-db: must be on data-net only" in p for p in problems(c)), problems(c)
c = cfg(extra={"openmrs-db": {"networks": {"data-net": {}}}, "gateway": {"networks": {"auth-net": {}, "data-net": {}}}})
c.setdefault("networks", {})["data-net"] = {"internal": False}
pr = problems(c)
assert any("gateway: must not be on data-net" in p for p in pr) and any("data-net must exist and be internal" in p for p in pr), pr
# KC-28-02: every compose volume is backed up or explicitly excluded
vols, excl = nac.backup_lists("VOLS=(a b\n  c)\nEXCLUDED=(d)   # why\n")
assert vols == {"a", "b", "c"} and excl == {"d"}, (vols, excl)
real_vols, real_excl = nac.backup_lists((nac.ROOT / "scripts" / "volume-backup.sh").read_text())
assert "keycloak-db-data" in real_vols and "gateway-logs" in real_vols
assert real_excl == {"certbot-www", "openelis-logs", "openelis-tomcat-logs"}, real_excl   # KC-45-03: logs stay on the host
c = cfg()
c["volumes"] = {v: {} for v in real_vols | real_excl | {"brand-new-data"}}
assert any("brand-new-data" in p and "KC-28-02" in p for p in problems(c)), problems(c)
print("nidan-auth-check: all cases OK (incl. -D secrets and backup coverage)")

# docker-compose.yml alone is plain Nidan: no Keycloak service, network or setting (keycloak.docker-compose.yml)
plain = {"services": {"odoo": {"environment": {"ODOO_DB_FILTER": "x"}, "networks": {"nidan-network": None}}}}
assert nac.plain_nidan_problems(plain) == []
leaky = {"services": {"keycloak": {}, "gateway": {"networks": {"auth-net": {}}},
                      "odoo": {"environment": {"NIDAN_SSO_ENABLED": "false", "NIDAN_KEYCLOAK_CLIENT_ID": "odoo"}}}}
assert len(nac.plain_nidan_problems(leaky)) == 4, nac.plain_nidan_problems(leaky)
# OpenELIS's login flags live in JAVA_OPTS with the Keycloak file: secrets there are caught too
j = {"services": {"openelis": {"environment": {"JAVA_OPTS": "-Dorg.itech.login.oauth.clientSecret=x"}}}}
assert any("JAVA_OPTS is logged" in p for p in nac.check(j)[1]), nac.check(j)[1]
assert not any("needs OPENMRS_AUTH" in p for p in nac.check(
    {"services": {"nidan-cis": {"environment": {"OPENMRS_AUTH_MODE": "jwt"}},
                  "openmrs-backend": {"environment": {"OPENMRS_SSO_ENABLED": "true"}}}}, secrets=False)[1])
print("plain-nidan / JAVA_OPTS checks ok")
