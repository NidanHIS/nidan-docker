"""Checks for the secrets tooling (backlog KC-05-01, KC-05-02, KC-05-04).

  python3 -I scripts/test_secrets_tooling.py

* generate-secrets.sh: refuses a tracked target; fills every placeholder with 40 random
  alphanumerics; two runs differ; paired keys match; file mode 0600; a re-run changes nothing.
* compose files: no secret-named variable falls back to a non-empty default (`${X:-value}`).
Scratch files go to tests/e2e-auth/test-results/ (git-ignored) under unique names; nothing is deleted.
"""
import os
import re
import stat
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRATCH = ROOT / "tests" / "e2e-auth" / "test-results"
PAIRS = {"OIS_WEBHOOK_SECRET": "NIDAN_OIS_SECRET", "ODOO_NIDAN_SECRET": "NIDAN_ODOO_SECRET",
         "OPENMRS_PASSWORD": "NIDAN_SYNC_PASSWORD", "OPENELIS_PASSWORD": "OPENELIS_DEFAULT_ADMIN_PASSWORD",
         "ODOO_PASSWORD": "ODOO_ADMIN_PASSWORD"}
OWNER_SUPPLIED = {"FEDERAL_HMIS_PASSWORD", "PROVINCIAL_DHIS2_PASSWORD", "BACKUP_AGE_RECIPIENT",
                  "SSL_TRUSTSTORE_PASSWORD", "SSL_KEYSTORE_PASSWORD"}


def generate(target):
    assert str(target) not in ("", ".env.local"), "never touch the real .env.local from a test"
    return subprocess.run(["sh", "scripts/generate-secrets.sh", "--fresh-install", str(target)],
                          cwd=ROOT, capture_output=True, text=True)


def values(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))


def test_generator():
    tracked = generate(".env")
    assert tracked.returncode != 0 and "tracked by git" in tracked.stderr, tracked.stderr

    SCRATCH.mkdir(parents=True, exist_ok=True)
    stamp = f"{int(time.time() * 1000)}-{os.getpid()}"
    a, b = SCRATCH / f"gen-a-{stamp}.local", SCRATCH / f"gen-b-{stamp}.local"
    for target in (a, b):
        target.write_text((ROOT / "env.template").read_text())
        r = generate(target.relative_to(ROOT))
        assert r.returncode == 0, r.stderr
        assert stat.S_IMODE(target.stat().st_mode) == 0o600, oct(target.stat().st_mode)
    va, vb = values(a), values(b)
    placeholders = {k for k, v in values(ROOT / "env.template").items() if v.startswith("CHANGEME")}
    generated = placeholders - OWNER_SUPPLIED
    for key in generated:
        size = 32 if key == "OAUTH2_PROXY_COOKIE_SECRET" else 40  # oauth2-proxy accepts only 16/24/32 bytes
        assert re.fullmatch(r"[A-Za-z0-9]{%d}" % size, va[key]), key
        if key not in PAIRS:
            assert va[key] != vb[key], f"{key} identical across runs"
    for follower, leader in PAIRS.items():
        assert va[follower] == va[leader], follower
    for key in OWNER_SUPPLIED & placeholders:
        assert va[key].startswith("CHANGEME"), f"{key} must be left for its owner"
    distinct = {va[k] for k in generated}
    assert len(distinct) == len(generated) - len(PAIRS), "only the paired keys may share values"

    before = a.read_text()
    assert generate(a.relative_to(ROOT)).returncode == 0
    assert a.read_text() == before, "re-run must not change existing values"


def test_compose_has_no_secret_defaults():
    secret = re.compile(r"\$\{([A-Z0-9_]*(?:PASSWORD|SECRET|_KEY|TOKEN)[A-Z0-9_]*):-([^}]+)\}")
    tracked = subprocess.run(["git", "ls-files", "docker-compose*.yml", "*.docker-compose.yml"],
                             cwd=ROOT, capture_output=True, text=True).stdout.split()
    allowed = {("dev.docker-compose.yml", "OPENELIS_DB_ROOT_PASSWORD")}  # not in .env; dev-only, tracked in progress log
    offenders = [(f, m.group(1)) for f in tracked for m in secret.finditer((ROOT / f).read_text())
                 if "BYPASS" not in m.group(1) and not m.group(1).endswith("_URL")  # e.g. *_TOKEN_URL
                 and not m.group(2).startswith("${")  # default is another variable, not a literal secret
                 and (f, m.group(1)) not in allowed]
    assert not offenders, offenders


if __name__ == "__main__":
    test_generator()
    test_compose_has_no_secret_defaults()
    print("secrets tooling checks: OK")
