"""Self-test for staff-config.py: `python3 -I scripts/test_staff_config.py`."""
import importlib.util
import io
import json
import os
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("sc", Path(__file__).resolve().parent / "staff-config.py")
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

STAFF = {"id": "aaaaaaaa-0000-4000-8000-000000000000", "name": "staff", "path": "/staff"}
N = "11111111-2222-4333-8444-555555555555"
kids = [{"id": N, "name": "Nurse", "path": "/staff/Nurse"},
        {"id": "not-a-uuid", "name": "Bad", "path": "/staff/Bad"},
        {"id": "22222222-3333-4444-8555-666666666666", "name": "Admins", "path": "/admins/Admins"},
        {"id": "33333333-4444-4555-8666-777777777777", "name": "x;}", "path": "/staff/x;}"}]
jobs, rejected = sc.jobs_from(STAFF, kids)
assert jobs == [(N, "Nurse", "/staff/Nurse")], jobs
assert len(rejected) == 3, rejected
try:
    sc.jobs_from({"id": "x", "path": "/admins"}, kids)
    raise AssertionError("a missing /staff group must stop the script")
except SystemExit:
    pass

cfg, groups_map = sc.render(jobs)
assert json.loads(cfg)["jobs"] == [{"id": N, "name": "Nurse", "path": "/staff/Nurse"}]
entries = [l for l in groups_map.splitlines() if not l.startswith("#")]
assert len(entries) == 3 and all(N in l for l in entries), entries
assert all(l.rstrip().endswith(";") or "#" in l for l in entries)
assert not any("(?<g>" in l for l in entries)          # no generic group capture: exact job ids only


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(req, timeout):
    assert req.headers["Authorization"] == "Bearer tok"
    return Resp(json.dumps(STAFF if req.full_url.endswith("/group-by-path/staff") else kids).encode())

with tempfile.TemporaryDirectory() as d:
    tok = Path(d) / "token"
    tok.write_text("tok\n")
    real = sc.fetch
    sc.fetch = lambda base, token, path: real(base, token, path, opener)
    os.environ["KC_TOKEN_FILE"] = str(tok)
    assert sc.main(["x", str(Path(d) / "out")]) == 0
    assert (Path(d) / "out" / "staff-groups.map").read_text() == groups_map
    os.environ["KC_TOKEN_FILE"] = ""
    assert sc.main(["x", str(Path(d) / "out")]) == 2
print("staff-config: OK")
