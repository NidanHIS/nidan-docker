"""Self-test for proxy/hsts-decide.sh (KC-12-02) with real openssl certificates: `python3 -I scripts/test_hsts_decide.py`."""
import os
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "proxy" / "hsts-decide.sh"


def sh(*cmd, cwd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


def decide(domain, cert, **env):
    return subprocess.run(["sh", str(SCRIPT), domain, str(cert)], capture_output=True, text=True,
                          env=dict(os.environ, **env)).stdout.strip()


with tempfile.TemporaryDirectory() as d:
    def ca(name, subj):
        sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{name}.key", "-out", f"{name}.pem",
           "-days", "2", "-subj", subj, cwd=d)

    def leaf(issuer, out):
        sh("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "l.key", "-out", "l.csr", "-subj",
           "/CN=ehr.example.org", cwd=d)
        sh("openssl", "x509", "-req", "-in", "l.csr", "-CA", f"{issuer}.pem", "-CAkey", f"{issuer}.key",
           "-CAcreateserial", "-out", out, "-days", "2", cwd=d)

    ca("self", "/CN=ehr.example.org")
    ca("siteca", "/CN=Nidan site CA (ehr)/O=Nidan EHR site")
    ca("public", "/CN=R11/O=Let's Encrypt/C=US")
    leaf("siteca", "leaf-site.pem")
    leaf("public", "leaf-public.pem")
    p = Path(d)
    env = {"GATEWAY_HSTS": "auto"}
    assert decide("ehr.example.org", p / "leaf-public.pem", **env) == "on"
    for domain, cert in (("ehr.example.org", "leaf-site.pem"), ("ehr.example.org", "self.pem"),
                         ("192.168.1.10", "leaf-public.pem"), ("localhost", "leaf-public.pem"),
                         ("fe80::1", "leaf-public.pem"), ("ehr.example.org", "missing.pem")):
        assert decide(domain, p / cert, **env) == "off", (domain, cert)
    assert decide("10.0.0.1", p / "x", GATEWAY_HSTS="on") == "on"
    assert decide("ehr.example.org", p / "leaf-public.pem", GATEWAY_HSTS="off") == "off"
print("hsts-decide: OK")
