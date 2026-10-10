"""Self-test for volume-backup.sh with a fake `docker` on PATH (KC-28-02): `python3 -I scripts/test_volume_backup.py`.

Checks: every existing volume is archived, absent ones are skipped (never created), exactly the project's running
containers are stopped and restarted, and restore by default takes the archives present in the backup.
KC-28-03: a failed or empty archive exits non-zero, writes no SHA256SUMS (never counts as a backup), still restarts
the stack and sets backup_succeeded 0. KC-28-04: no credential on any docker command line. KC-28-05: refuses to back
up while BACKUP_AGE_IDENTITY (the private key) is configured on the host.
"""
import os
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "volume-backup.sh"
FAKE = r'''#!/bin/sh
echo "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "image inspect") exit 0 ;;
  "volume inspect") case " $PRESENT " in *" $3 "*) exit 0 ;; *) exit 1 ;; esac ;;
  "volume create") exit 0 ;;
esac
case "$1" in
  ps) echo c-odoo; echo c-keycloak; exit 0 ;;
  stop) exit 0 ;;
  start) [ -n "${FAIL_START:-}" ] && exit 1; exit 0 ;;
  run)
    out=""; vol=""
    for a in "$@"; do case "$a" in *:/out) out="${a%:/out}" ;; *:/v:ro) vol="${a%:/v:ro}" ;; esac; done
    if [ -n "$out" ]; then
      v="${vol#nidan-docker_}"
      [ -n "${SLOW:-}" ] && sleep 5
      [ "$v" = "${FAIL_VOL:-}" ] && exit 1
      if [ "$v" = "${EMPTY_VOL:-}" ]; then : > "$out/$v.tar.gz.age.part"; else printf 'age-encryption.org/v1\n-> X25519 x\n' > "$out/$v.tar.gz.age.part"; fi
    fi
    exit 0 ;;
esac
exit 0
'''

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    (d / "bin").mkdir()
    fake = d / "bin" / "docker"
    fake.write_text(FAKE)
    fake.chmod(0o755)
    log = d / "docker.log"
    out = d / "backup"
    present = "nidan-docker_odoo-data nidan-docker_keycloak-db-data nidan-docker_gateway-logs"
    env = dict(os.environ, PATH=f"{d / 'bin'}:{os.environ['PATH']}", FAKE_LOG=str(log), PRESENT=present,
               COMPOSE_PROJECT_NAME="nidan-docker", METRICS_DIR=str(d / "metrics"),
               OPENMRS_DB_PASSWORD="db-secret-must-not-leak", KEYCLOAK_ADMIN_PASSWORD="kc-secret-must-not-leak",
               BACKUP_AGE_RECIPIENT="age1" + "q" * 58)
    r = subprocess.run(["bash", str(SCRIPT), "backup", str(out)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    archives = sorted(p.name for p in out.glob("*.tar.gz.age"))
    assert archives == ["gateway-logs.tar.gz.age", "keycloak-db-data.tar.gz.age", "odoo-data.tar.gz.age"], archives
    assert "openmrs-db-data: not on this host, skipped" in r.stdout
    calls = log.read_text().splitlines()
    assert not any(c.startswith("volume create") for c in calls), "backup must never create a volume"
    assert "stop c-odoo c-keycloak" in calls and "start c-odoo c-keycloak" in calls, calls
    assert calls.index("stop c-odoo c-keycloak") < min(i for i, c in enumerate(calls) if c.startswith("run"))
    assert (out / "SHA256SUMS").exists()
    assert "backup_succeeded 1" in (d / "metrics" / "nidan_backup.prom").read_text()
    assert "must-not-leak" not in log.read_text(), "a credential reached a docker command line"

    # KC-28-03: a failing archive step and an empty archive both fail the run, leave no SHA256SUMS, restart the stack
    for bad in ({"FAIL_VOL": "keycloak-db-data"}, {"EMPTY_VOL": "odoo-data"}):
        log.write_text("")
        failed = d / f"failed-{sorted(bad)[0]}"
        r = subprocess.run(["bash", str(SCRIPT), "backup", str(failed)], env=dict(env, **bad), capture_output=True, text=True)
        assert r.returncode != 0 and "BACKUP FAILED" in r.stderr, (bad, r.stdout, r.stderr)
        assert not (failed / "SHA256SUMS").exists(), bad
        assert "start c-odoo c-keycloak" in log.read_text().splitlines(), ("stack left stopped", bad)
        assert "backup_succeeded 0" in (d / "metrics" / "nidan_backup.prom").read_text(), bad
    assert "archive is empty or not age-encrypted" in r.stderr

    # a stack that does not come back after a good backup is a failure too (not "backup_succeeded 1")
    log.write_text("")
    r = subprocess.run(["bash", str(SCRIPT), "backup", str(d / "restart-fails")], env=dict(env, FAIL_START="1"),
                       capture_output=True, text=True)
    assert r.returncode != 0 and "could not start the stack again" in r.stderr, (r.stdout, r.stderr)
    assert "backup_succeeded 0" in (d / "metrics" / "nidan_backup.prom").read_text()

    # an interrupted run (cron timeout, Ctrl-C) is reported as failed and the stack still restarts
    import signal, time
    log.write_text("")
    p = subprocess.Popen(["bash", str(SCRIPT), "backup", str(d / "interrupted")], env=dict(env, SLOW="1"),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1.5)
    p.send_signal(signal.SIGTERM)
    _, err = p.communicate(timeout=30)
    assert p.returncode != 0 and "BACKUP FAILED" in err, (p.returncode, err)
    assert "start c-odoo c-keycloak" in log.read_text().splitlines(), "stack left stopped after an interrupt"
    assert not (d / "interrupted" / "SHA256SUMS").exists()

    # KC-28-05: the private key must not be configured on the stack host
    r = subprocess.run(["bash", str(SCRIPT), "backup", str(d / "with-key")], env=dict(env, BACKUP_AGE_IDENTITY="/root/k"),
                       capture_output=True, text=True)
    assert r.returncode != 0 and "private key must not be on the stack host" in r.stderr, r.stderr

    # restore with no volume list takes exactly the archives in the backup
    key = d / "key.txt"
    key.write_text("AGE-SECRET-KEY-1TEST\n")
    log.write_text("")
    env["BACKUP_AGE_IDENTITY"] = str(key)
    r = subprocess.run(["bash", str(SCRIPT), "restore", str(out)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    created = [c.split()[-1] for c in log.read_text().splitlines() if c.startswith("volume create")]
    assert sorted(created) == ["nidan-docker_gateway-logs", "nidan-docker_keycloak-db-data", "nidan-docker_odoo-data"], created

    # inside the repository is refused
    env.pop("BACKUP_AGE_IDENTITY")
    r = subprocess.run(["bash", str(SCRIPT), "backup", str(SCRIPT.parent.parent / "tmp-backup-test")], env=env,
                       capture_output=True, text=True)
    assert r.returncode != 0 and "inside the repository" in r.stderr
    assert not (SCRIPT.parent.parent / "tmp-backup-test").exists()

# retention (KC-28-08): 14 newest + newest of each month up to 12 months; dry run by default
import datetime
with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    day0 = datetime.date(2026, 10, 8)
    names = []
    for i in range(400):
        n = (day0 - datetime.timedelta(days=i)).strftime("%Y%m%d") + "-001500"
        (root / n).mkdir()
        (root / n / "SHA256SUMS").write_text("x")
        names.append(n)
    (root / "20261008-120000").mkdir()             # incomplete (no SHA256SUMS): never pruned
    (root / "keep-me").mkdir()                     # not a backup folder: never touched
    dry = subprocess.run(["bash", str(SCRIPT), "prune", str(root)], capture_output=True, text=True)
    assert dry.returncode == 0 and "dry run" in dry.stdout, dry.stdout + dry.stderr
    assert len(list(root.iterdir())) == 402, "dry run deleted something"
    r = subprocess.run(["bash", str(SCRIPT), "prune", str(root), "--execute"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    left = sorted(p.name for p in root.iterdir() if p.name[:2] == "20" and (p / "SHA256SUMS").exists())
    assert left[-14:] == sorted(names[:14]), left[-14:]           # the 14 newest
    months = {n[:6] for n in left}
    assert len(months) == 12, sorted(months)
    assert len(left) == 14 + (12 - len({n[:6] for n in names[:14]})), len(left)
    for m in months:                                                # newest of each month kept
        assert max(n for n in names if n[:6] == m) in left, m
    assert (root / "20261008-120000").exists() and (root / "keep-me").exists()
print("volume-backup: OK")
