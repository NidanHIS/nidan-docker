"""Self-test for check-backup-safety.py: `python3 -I scripts/test_check_backup_safety.py`."""
import importlib.util
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("cbs", Path(__file__).resolve().parent / "check-backup-safety.py")
cbs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cbs)

with tempfile.TemporaryDirectory() as d:
    cbs.ROOT = Path(d)
    f = Path(d) / "b.sh"
    f.write_text("""#!/bin/sh
docker exec db pg_dump -U x clinlims > /out/dump.sql
docker exec db pg_dump -U x clinlims | age -r "$R" -o /out/dump.age
docker exec db mysqldump -u root --password=hunter2 openmrs \\
  | gzip | age -r "$R" > /out/o.age
docker exec db pg_dump -U x a \\
  > /out/plain.sql
# docker exec db pg_dump -U x commented > /tmp/x
docker exec db sh -c 'MYSQL_PWD="$P" mysqldump -u root openmrs' | age -r "$R" > /out/ok.age
""")
    got = cbs.problems([f])
    lines = sorted({int(p.split(":")[1]) for p in got})
    assert lines == [2, 4, 6], got                         # plaintext, argv password (piped), plaintext (continued)
    assert sum("not piped through age" in p for p in got) == 2, got
    assert sum("password on the command line" in p for p in got) == 1, got
print("check-backup-safety: OK")
