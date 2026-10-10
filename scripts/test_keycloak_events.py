"""Self-test for keycloak-event-shipper.py and keycloak-reapply-after-restore.py: `python3 -I scripts/test_keycloak_events.py`.
Live run: scripts/keycloak-restore-drill.sh (restore-drill.yml)."""
import importlib.util
import json
import stat
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ship = load("ship", "keycloak-event-shipper.py")
reapply = load("reapply", "keycloak-reapply-after-restore.py")
U = "11111111-2222-4333-8444-555555555555"
G = "22222222-3333-4444-8555-666666666666"
DAY = 86_400_000

admin_events = [  # newest first, as Keycloak lists them
    {"time": 5 * DAY, "operationType": "DELETE", "resourceType": "GROUP_MEMBERSHIP", "resourcePath": f"users/{U}/groups/{G}"},
    {"time": 4 * DAY, "operationType": "UPDATE", "resourceType": "USER", "resourcePath": f"users/{U}", "representation": '{"enabled":true}'},
    {"time": 3 * DAY, "operationType": "UPDATE", "resourceType": "USER", "resourcePath": f"users/{U}", "representation": '{"enabled":false}'},
    {"time": 2 * DAY, "operationType": "UPDATE", "resourceType": "REALM", "representation": "{}"},
    {"time": 1 * DAY, "operationType": "CREATE", "resourceType": "USER", "resourcePath": f"users/{U}", "representation": '{"enabled":true}'},
]
store = {"/events": [], "/admin-events": list(admin_events)}
get = lambda path: store[path.split("?")[0]] if "first=0" in path else []

with tempfile.TemporaryDirectory() as d:
    out = Path(d) / "events"
    assert ship.ship(get, out) == {"login": 0, "admin": 5}
    assert ship.ship(get, out) == {"login": 0, "admin": 0}                      # nothing twice
    store["/admin-events"].insert(0, {"time": 6 * DAY, "operationType": "DELETE", "resourceType": "USER", "resourcePath": f"users/{U}"})
    assert ship.ship(get, out) == {"login": 0, "admin": 1}
    files = sorted(out.glob("admin-events-*.jsonl"))
    lines = [json.loads(l) for f in files for l in f.read_text().splitlines()]
    assert [e["time"] for e in lines] == sorted(e["time"] for e in lines)      # oldest first
    assert stat.S_IMODE(out.stat().st_mode) == 0o700 and all(stat.S_IMODE(f.stat().st_mode) == 0o600 for f in files)
    store["/admin-events"] = []                                                 # events deleted in Keycloak...
    ship.ship(get, out)
    assert sum(len(f.read_text().splitlines()) for f in files) == 6             # ...the shipped copy keeps them

    # re-apply: only what takes access away, after the backup time, oldest first; never the re-enable
    assert reapply.revocations(out, 2 * DAY) == [(3 * DAY, "disable", U, None), (5 * DAY, "leave-group", U, G),
                                                 (6 * DAY, "disable", U, None)], reapply.revocations(out, 2 * DAY)
    assert reapply.revocations(out, 6 * DAY) == []
    assert reapply.parse_since("2026-10-09T00:00:00Z") == 1791504000000
print("keycloak events: OK")
