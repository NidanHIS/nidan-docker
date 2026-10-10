"""Checks for scripts/site-ca.sh (backlog KC-12-04/05): `python3 -I scripts/test_site_ca.py`.

Writes a throwaway CA to a new temp dir outside the repo (left in place; nothing is deleted).
"""
import stat
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*args, ok=True):
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    assert (r.returncode == 0) == ok, (args, r.stdout, r.stderr)
    return r


out = Path(tempfile.mkdtemp(prefix="nidan-site-ca-")) / "site"
run("sh", "scripts/site-ca.sh", str(out), "192.168.10.5", "ehr.hospital.lan")
ca, leaf = out / "ca" / "ca.crt", out / "gateway" / "cert.pem"

for key in (out / "ca" / "ca.key", out / "gateway" / "privkey.pem"):
    assert stat.S_IMODE(key.stat().st_mode) == 0o600, key
ext = run("openssl", "x509", "-in", str(ca), "-noout", "-ext", "basicConstraints,nameConstraints").stdout
assert "critical" in ext and "CA:TRUE, pathlen:0" in ext, ext
assert "IP:192.168.10.5/255.255.255.255" in ext and "DNS:ehr.hospital.lan" in ext, ext
san = run("openssl", "x509", "-in", str(leaf), "-noout", "-ext", "subjectAltName").stdout
assert "IP Address:192.168.10.5" in san and "DNS:ehr.hospital.lan" in san, san
run("openssl", "verify", "-CAfile", str(ca), str(leaf))

# A certificate for any other name, even signed with the real CA key, must not verify.
work = out.parent
run("openssl", "req", "-new", "-nodes", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
    "-subj", "/CN=example.com", "-keyout", str(work / "rogue.key"), "-out", str(work / "rogue.csr"))
for i, name in enumerate(("DNS:example.com", "IP:10.0.0.9")):
    (work / f"r{i}.cnf").write_text(f"subjectAltName={name}\nextendedKeyUsage=serverAuth\n")
    run("openssl", "x509", "-req", "-in", str(work / "rogue.csr"), "-CA", str(ca), "-CAkey", str(out / "ca" / "ca.key"),
        "-CAcreateserial", "-days", "30", "-extfile", str(work / f"r{i}.cnf"), "-out", str(work / f"r{i}.crt"))
    run("openssl", "verify", "-CAfile", str(ca), str(work / f"r{i}.crt"), ok=False)

# Refusals: a non-empty target, and a target inside a git checkout (nothing created there).
run("sh", "scripts/site-ca.sh", str(out), "1.2.3.4", ok=False)
inside = ROOT / "tests" / "e2e-auth" / "test-results" / f"ca-refused-{work.name}"
run("sh", "scripts/site-ca.sh", str(inside), "1.2.3.4", ok=False)
assert not inside.exists(), "site-ca.sh created a directory inside the repo"
print("site-ca checks: OK")
