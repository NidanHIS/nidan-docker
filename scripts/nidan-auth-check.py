#!/usr/bin/env python3
"""Print each component's auth mode and fail on contradictions (backlog KC-27-02/03/05, KC-14-06).

  COMPOSE_ENV_FILES=.env,.env.local scripts/nidan-auth-check.py

Reads the effective config (`docker compose --profile '*' config --format json`; no daemon needed).
Fails (exit 1) when:
  * a machine caller (CIS, Odoo) uses OPENMRS_AUTH_MODE=jwt while OpenMRS SSO is off, or the two
    callers disagree on the mode, or jwt is set without its token URL / client secret
  * a paywall URL (OpenMRS nidancore, OpenELIS) points at the public gateway instead of odoo:8069
    (a gateway 401 is treated as a billing outage, so the paywall would silently switch off)
  * a service publishes a host port outside the allow-list
  * a service has no log size cap (KC-13-04)
  * a service other than gateway / nidan-certbot mounts the TLS private keys (KC-12-06)
  * auth-net holds anything but gateway / oauth2-proxy / keycloak / keycloak-db / keycloak-config-cli,
    keycloak* sit on another network too, or auth-net is not internal (KC-43-04, static part)
  * a database / Kafka / ZooKeeper is on anything but data-net, the gateway, oauth2-proxy, Keycloak or a frontend is
    on data-net, or data-net is not internal (KC-43-03, static part; tests/e2e-auth/harness/net-probe.py is the live one)
  * oauth2-proxy (KC-10-13): older than 7.15.2, any skip_auth_* rule, skip_jwt_bearer_tokens on,
    cookie_expire over 12h, PKCE not S256, or reverse_proxy while reachable by anything but auth-net
  * the OpenMRS distro still bundles the `authentication` module, which silently disables oauth2login
    (KC-34-01; reported as pending until its removal is approved)
  * OpenELIS is given -Dorg.itech.login.form=false: that drops its catch-all `anyRequest().authenticated()`
    filter chain, leaving /rest/** with no security chain at all (KC-38; keep form login on, the gateway
    refuses /ValidateLogin when central login is on)
  * a secret is passed as a -D flag in CATALINA_OPTS (Tomcat logs every JVM argument at start, KC-45-01);
    datasource.password is reported only (Tomcat reads it from system properties for context.xml)
  * a named volume is neither backed up nor explicitly excluded in scripts/volume-backup.sh (KC-28-02)
  * the gateway is not built from ./proxy (or its published trigonaltechnology/gateway image), or
    anything uses nidan-proxy-image (KC-01-08)
Values of secrets are never printed.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# service -> allowed published ports ("host_ip:port" or "port"); KC-02 target state.
PORT_ALLOW = {
    "gateway": {"80", "443", "8443", "8069", "9443"},
    "orthanc": {"4242"},
    "keycloak-admin-proxy": {"127.0.0.1:8180"},
}
# Published ports still awaiting a closing decision (KC-02); reported, not failed. None since 2026-10-10.
PORT_PENDING = set()

# TLS private keys (certbot-etc holds every privkey.pem; keys/ holds nginx.key).
KEY_MOUNT_OK = {"gateway", "nidan-certbot"}
# orthanc mounts certbot-etc read-only but has no TLS setting; removal awaits the user's nod (KC-12-06).
KEY_MOUNT_PENDING = {"orthanc"}
# keycloak-role-sync: Keycloak admin API (auth-net) + the apps' role lists (nidan-network); no published ports
AUTH_NET_MEMBERS = {"gateway", "oauth2-proxy", "keycloak", "keycloak-db", "keycloak-config-cli", "keycloak-admin-proxy",
                    "keycloak-role-sync"}
AUTH_ONLY = {"keycloak", "keycloak-db", "keycloak-config-cli"}
# Databases, Kafka, ZooKeeper: on data-net only, which the gateway and the central-login services never join (KC-43-03)
DATA_ONLY = {"openmrs-db", "odoo-db", "openelis-db", "orthanc-db", "nidan-integration-db", "nidan-dhis2-db", "nidan-kafka",
             "nidan-zookeeper"}
NEVER_ON_DATA_NET = AUTH_NET_MEMBERS | {"openmrs-frontend", "openelis-frontend", "nidan-certbot"}
GATEWAY_IMAGE = re.compile(r"^(docker\.io/)?trigonaltechnology/gateway[:@]")


def key_mounts(service):
    out = []
    for v in service.get("volumes") or []:
        src, tgt = (v.get("source", ""), v.get("target", "")) if isinstance(v, dict) else (str(v).split(":") + [""])[:2]
        if (src == "certbot-etc" or str(tgt).startswith("/etc/letsencrypt") or str(src).endswith((".key", "privkey.pem"))
                or "/keys" in str(src).replace("\\", "/")):
            out.append(f"{src} -> {tgt}")
    return out


# Removing omod.authentication from the distro awaits the user's nod (D37); until then report, not fail.
AUTHENTICATION_OMOD_PENDING = True


def openmrs_distro_lines(root=ROOT):
    """Files and lines that bundle the OpenMRS `authentication` module (KC-34-01)."""
    out = []
    for f in sorted((root / "openmrs" / "distro").rglob("*")):
        if f.is_file() and (f.suffix == ".properties" or f.name == "pom.xml") and "target" not in f.parts:
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"omod\.authentication\s*=|<artifactId>authentication-omod</artifactId>", line):
                    out.append(f"{f.relative_to(root)}:{n}")
    return out


def backup_lists(text):
    """(VOLS, EXCLUDED) bash arrays of scripts/volume-backup.sh."""
    def arr(name):
        m = re.search(name + r"=\(([^)]*)\)", text)
        return set(re.sub(r"#[^\n]*", "", m.group(1)).split()) if m else set()
    return arr("VOLS"), arr("EXCLUDED")


def oauth2_proxy_problems(svc, cfg_text, gateway_ip="172.31.250.10"):
    """KC-10-13 on the merged service + proxy/oauth2-proxy.cfg (env OAUTH2_PROXY_* overrides the file)."""
    import tomllib
    out = []
    m = re.search(r":v?(\d+)\.(\d+)\.(\d+)", str(svc.get("image", "")))
    if not m or tuple(map(int, m.groups())) < (7, 15, 2):
        out.append(f"oauth2-proxy: image {svc.get('image')} is older than 7.15.2 (auth-bypass fixes)")
    cfg = tomllib.loads(cfg_text)
    for k, v in env_of(svc).items():
        if k.startswith("OAUTH2_PROXY_"):
            cfg[k[len("OAUTH2_PROXY_"):].lower()] = v
    for k in cfg:
        if k.startswith("skip_auth"):
            out.append(f"oauth2-proxy: {k} is set (gate-by-default: no skip rules, KC-10-13)")
    if str(cfg.get("skip_jwt_bearer_tokens", False)).lower() not in ("false", "0"):
        out.append("oauth2-proxy: skip_jwt_bearer_tokens must be false")
    exp = re.fullmatch(r"(\d+)([hms])", str(cfg.get("cookie_expire", "168h")))
    secs = int(exp.group(1)) * {"h": 3600, "m": 60, "s": 1}[exp.group(2)] if exp else 10 ** 9
    if secs > 12 * 3600:
        out.append(f"oauth2-proxy: cookie_expire {cfg.get('cookie_expire', '168h (default)')} exceeds 12h")
    if cfg.get("code_challenge_method") != "S256":
        out.append("oauth2-proxy: code_challenge_method must be S256")
    if str(cfg.get("reverse_proxy", False)).lower() in ("true", "1"):
        nets = set(svc.get("networks") or {})
        if nets != {"auth-net"} or svc.get("ports"):
            out.append(f"oauth2-proxy: reverse_proxy trusts X-Forwarded-*; it must be reachable only on auth-net "
                       f"(networks {sorted(nets)}, ports {svc.get('ports') or 'none'})")
        trusted = cfg.get("trusted_proxy_ips") or []
        trusted = [t.strip() for t in trusted.split(",")] if isinstance(trusted, str) else list(trusted)
        if sorted(t for t in trusted if t) not in ([gateway_ip], [gateway_ip + "/32"]):
            out.append(f"oauth2-proxy: trusted_proxy_ips must be only the gateway ({gateway_ip}/32), not "
                       f"{trusted or 'unset (trusts every IP)'}")
    return out


def env_of(service):
    env = service.get("environment") or {}
    if isinstance(env, list):
        env = dict(e.split("=", 1) if "=" in e else (e, "") for e in env)
    return {k: ("" if v is None else str(v)) for k, v in env.items()}


def truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def check(config):
    """Return (report_lines, problems) for a compose config dict."""
    services = config.get("services", {})
    envs = {name: env_of(s) for name, s in services.items()}
    lines, problems = [], []

    any_env = next(iter(envs.values()), {})
    sso = truthy(next((e.get("NIDAN_SSO_ENABLED") for e in envs.values() if e.get("NIDAN_SSO_ENABLED")), "false"))
    openmrs_sso_raw = next((e.get("OPENMRS_SSO_ENABLED") for e in envs.values() if e.get("OPENMRS_SSO_ENABLED")), "")
    openmrs_sso = truthy(openmrs_sso_raw) if openmrs_sso_raw else sso
    lines.append(f"central login (NIDAN_SSO_ENABLED): {'on' if sso else 'off'}")
    lines.append(f"OpenMRS SSO: {'on' if openmrs_sso else 'off'}")

    callers = {"nidan-cis": "CIS", "odoo": "Odoo"}
    modes = {}
    for svc, label in callers.items():
        e = envs.get(svc)
        if e is None:
            continue
        mode = (e.get("OPENMRS_AUTH_MODE") or "basic").strip().lower()
        modes[label] = mode
        lines.append(f"{label} -> OpenMRS auth: {mode}")
        if mode not in ("basic", "jwt"):
            problems.append(f"{label}: OPENMRS_AUTH_MODE must be basic or jwt, got {mode!r}")
        if mode == "jwt":
            if not openmrs_sso:
                problems.append(f"{label}: OPENMRS_AUTH_MODE=jwt but OpenMRS SSO is off (OpenMRS would refuse the token)")
            for k in ("OPENMRS_AUTH_TOKEN_URL", "OPENMRS_AUTH_CLIENT_SECRET"):
                if not e.get(k):
                    problems.append(f"{label}: OPENMRS_AUTH_MODE=jwt needs {k}")
        elif openmrs_sso:
            problems.append(f"{label}: OpenMRS SSO is on but {label} still uses basic (Basic auth is off in OpenMRS)")
    if len(set(modes.values())) > 1:
        problems.append(f"machine callers disagree on OPENMRS_AUTH_MODE: {modes}")

    gateway_host = (envs.get("gateway", {}).get("GATEWAY_DOMAIN") or any_env.get("GATEWAY_DOMAIN") or "").strip()
    paywall_urls = []
    for svc, e in envs.items():
        if e.get("NIDANCORE_ODOO_URL"):
            paywall_urls.append((svc, "NIDANCORE_ODOO_URL", e["NIDANCORE_ODOO_URL"]))
        m = re.search(r"-Dorg\.openelisglobal\.nidan\.paywall\.odoo\.url=(\S+)", e.get("CATALINA_OPTS", ""))
        if m:
            paywall_urls.append((svc, "paywall.odoo.url", m.group(1)))
    for svc, key, url in paywall_urls:
        host = re.sub(r"^\w+://", "", url).split("/")[0].split(":")[0]
        internal = host in ("odoo", "nidan-odoo")
        lines.append(f"{svc} paywall -> {url} ({'internal' if internal else 'NOT internal'})")
        if not internal and (host == gateway_host or host in ("localhost", "127.0.0.1") or "." in host):
            problems.append(f"{svc}: {key} points at {host}, not the internal odoo:8069 (a gateway 401 = paywall off)")

    for name, s in services.items():
        for p in s.get("ports") or []:
            published = str(p.get("published", "")) if isinstance(p, dict) else str(p).split(":")[-2]
            host_ip = p.get("host_ip", "") if isinstance(p, dict) else ""
            key = f"{host_ip}:{published}" if host_ip and host_ip != "0.0.0.0" else published
            if key in PORT_ALLOW.get(name, set()) or published in PORT_ALLOW.get(name, set()) - {"127.0.0.1:8180"}:
                continue
            if (name, published) in PORT_PENDING:
                lines.append(f"port {published} on {name}: still published (pending approval to close, KC-02)")
                continue
            problems.append(f"{name}: publishes host port {key} outside the allow-list")
    for name, s in services.items():
        for m in key_mounts(s):
            if name in KEY_MOUNT_OK:
                continue
            if name in KEY_MOUNT_PENDING:
                lines.append(f"{name}: mounts TLS keys ({m}) without using them (pending approval to remove, KC-12-06)")
            else:
                problems.append(f"{name}: mounts TLS private keys ({m}); only {sorted(KEY_MOUNT_OK)} may (KC-12-06)")
        build = s.get("build") or {}
        ctx = str(build.get("context", "") if isinstance(build, dict) else build)
        if "nidan-proxy-image" in ctx or "nidan-proxy" in str(s.get("image", "")):
            problems.append(f"{name}: uses nidan-proxy-image; only ./proxy is the gateway (KC-01-08)")
        if name == "gateway":
            if ctx and not ctx.rstrip("/").endswith("/proxy") and ctx.rstrip("/") != "proxy":
                problems.append(f"gateway: built from {ctx}, not ./proxy (KC-01-08)")
            elif not ctx and s.get("image") and not GATEWAY_IMAGE.match(str(s["image"])):
                problems.append(f"gateway: image {s['image']} is not trigonaltechnology/gateway built from ./proxy (KC-01-08)")
    for where in openmrs_distro_lines():
        msg = f"{where}: bundles the OpenMRS authentication module (disables oauth2login, KC-34-01)"
        if AUTHENTICATION_OMOD_PENDING:
            lines.append(msg + " - pending approval to remove")
        else:
            problems.append(msg)
    for name, svc in services.items():
        for flag in re.findall(r"-D([\w.]*(?:secret|password|passwd|token|key))=", env_of(svc).get("CATALINA_OPTS", ""),
                               re.I):
            if flag == "datasource.password":
                lines.append(f"{name}: -Ddatasource.password is logged by Tomcat at start (KC-45-01 residual)")
            else:
                problems.append(f"{name}: secret -D{flag} in CATALINA_OPTS is logged by Tomcat; pass it as an env var")
    for name, svc in services.items():
        if re.search(r"-Dorg\.itech\.login\.form=(false|no|0)\b", env_of(svc).get("CATALINA_OPTS", ""), re.I):
            problems.append(f"{name}: -Dorg.itech.login.form=false removes OpenELIS's default authenticated filter "
                            "chain (REST paths would be unauthenticated); keep it true (KC-38)")
    backup = ROOT / "scripts" / "volume-backup.sh"
    if backup.exists():
        vols, excluded = backup_lists(backup.read_text())
        for v in sorted(set(config.get("volumes") or {}) - vols - excluded):
            problems.append(f"volume {v} is not in scripts/volume-backup.sh VOLS (nor EXCLUDED with a reason) (KC-28-02)")
        for v in sorted((vols | excluded) - set(config.get("volumes") or {})):
            lines.append(f"volume-backup.sh lists {v}, which compose no longer defines")
    if "oauth2-proxy" in services:
        cfg = ROOT / "proxy" / "oauth2-proxy.cfg"
        gw_ip = (((services.get("gateway") or {}).get("networks") or {}).get("auth-net") or {}).get("ipv4_address", "")
        problems += oauth2_proxy_problems(services["oauth2-proxy"], cfg.read_text() if cfg.exists() else "", gw_ip)
    if "auth-net" in (config.get("networks") or {}):
        if not (config["networks"]["auth-net"] or {}).get("internal"):
            problems.append("auth-net must be internal (no route out) (KC-43)")
    if any(n in services for n in DATA_ONLY) and not ((config.get("networks") or {}).get("data-net") or {}).get("internal"):
        problems.append("data-net must exist and be internal (no route out) (KC-43-03)")
    for name, s in services.items():
        nets = set(s.get("networks") or {})
        if "auth-net" in nets and name not in AUTH_NET_MEMBERS:
            problems.append(f"{name}: on auth-net; only {sorted(AUTH_NET_MEMBERS)} may be (KC-43-04)")
        if name in AUTH_ONLY and nets != {"auth-net"}:
            problems.append(f"{name}: must be on auth-net only, is on {sorted(nets)} (KC-43-04)")
        if "kc-admin-net" in nets and name != "keycloak-admin-proxy":
            problems.append(f"{name}: on kc-admin-net; only keycloak-admin-proxy may be (KC-07-04)")
        internal = {n for n, v in (config.get("networks") or {}).items() if (v or {}).get("internal")}
        if s.get("ports") and nets and nets <= internal:
            problems.append(f"{name}: publishes ports but is only on internal networks {sorted(nets)}: Docker does not "
                            "serve them (KC-07-04: use keycloak-admin-proxy)")
        if name in DATA_ONLY and nets != {"data-net"}:
            problems.append(f"{name}: must be on data-net only, is on {sorted(nets)} (KC-43-03)")
        if name in NEVER_ON_DATA_NET and "data-net" in nets:
            problems.append(f"{name}: must not be on data-net (databases and Kafka) (KC-43-03)")
    for name, s in services.items():
        if not ((s.get("logging") or {}).get("options") or {}).get("max-size"):
            problems.append(f"{name}: no logging max-size (unbounded json-file logs fill the disk)")
    return lines, problems


def main():
    out = subprocess.run(["docker", "compose", "--profile", "*", "config", "--format", "json"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        print(out.stderr.strip(), file=sys.stderr)
        return 1
    lines, problems = check(json.loads(out.stdout))
    for line in lines:
        print(line)
    print(f"\n{len(problems)} contradiction(s)")
    for p in problems:
        print("  " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
