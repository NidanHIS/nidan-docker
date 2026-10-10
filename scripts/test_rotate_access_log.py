"""Self-test for proxy/rotate-access-log.sh (KC-13-03): `python3 -I scripts/test_rotate_access_log.py`."""
import os
import stat
import subprocess
import tempfile
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "proxy" / "rotate-access-log.sh"

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    logs = d / "logs"
    logs.mkdir()
    fake = d / "nginx"
    fake.write_text(f"#!/bin/sh\necho \"$@\" >> {d}/nginx.calls\n: > {logs}/access.log\n")
    fake.chmod(0o755)
    (logs / "access.log").write_text('[08/Oct/2026] "GET /openmrs/ HTTP/1.1" status:200 user:nurse1 app:openmrs\n')
    old = logs / "access-20250901-001500.log.gz"
    young = logs / "access-20260901-001500.log.gz"
    other = logs / "error.log"
    for f in (old, young, other):
        f.write_text("x")
    long_ago = time.time() - 400 * 86400
    os.utime(old, (long_ago, long_ago))
    os.utime(other, (long_ago, long_ago))

    env = dict(os.environ, NGINX=str(fake))
    subprocess.run(["sh", str(SCRIPT), str(logs), "365"], check=True, env=env)
    rotated = sorted(p.name for p in logs.glob("access-2026*.log.gz") if p != young)
    assert len(rotated) == 1, rotated
    assert stat.S_IMODE((logs / rotated[0]).stat().st_mode) == 0o600
    assert not old.exists() and young.exists() and other.exists(), sorted(p.name for p in logs.iterdir())
    assert (d / "nginx.calls").read_text().strip() == "-s reopen"
    assert (logs / "access.log").exists() and (logs / "access.log").stat().st_size == 0

    # an empty log is left alone (no reopen, no empty archive)
    subprocess.run(["sh", str(SCRIPT), str(logs), "365"], check=True, env=env)
    assert (d / "nginx.calls").read_text().count("reopen") == 1
print("rotate-access-log: OK")
