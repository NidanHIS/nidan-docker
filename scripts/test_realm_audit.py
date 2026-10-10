"""Self-test for realm-audit.py: `python3 -I scripts/test_realm_audit.py` (live run: realm-import.yml)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("ra", Path(__file__).resolve().parent / "realm-audit.py")
ra = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ra)


class Fake:
    def __init__(self, default=({"name": "view-profile", "clientRole": True},)):
        self.default = list(default)
        self.groups = [{"id": g.strip("/"), "path": g} for g in ra.ADMIN_GROUPS + ["/staff"]]
        self.m = {"hospital-admin": [{"id": "u1", "username": "hadmin1"}, {"id": "u2", "username": "hadmin2"}],
                  "app-superusers": [{"id": "u3", "username": "super1"}], "cis-admin": [], "orthanc-admin": [],
                  "nurse": [{"id": "u4", "username": "nurse1"}, {"id": "u2", "username": "hadmin2"}]}
        self.otp = {"u1", "u3"}

    users = []
    puts = []

    def put(self, path, body):
        self.puts.append((path, body))

    def get(self, path):
        if path.startswith("/users?"):
            return self.users if "first=0" in path else []
        if path.startswith("/users/") and path.split("/")[2].startswith("t"):
            return [{"type": "password", "createdDate": {"t1": 0, "t2": 6 * 86_400_000}[path.split("/")[2]]}]
        if path.startswith("/groups?"):
            return self.groups
        if path.startswith("/groups/staff/children"):
            return [{"id": "nurse", "path": "/staff/Nurse"}]
        if path.startswith("/groups/") and "/members" in path:
            return self.m[path.split("/")[2]] if "first=0" in path else []
        if path.startswith("/users/"):
            return [{"type": "otp"}] if path.split("/")[2] in self.otp else [{"type": "password"}]
        if path == "/roles/default-roles-nidan/composites":
            return self.default
        raise AssertionError(path)


out = ra.findings(Fake())
assert out == ["hadmin2 (/hospital-admin): no OTP enrolled", "hadmin2: in /staff/Nurse and in /hospital-admin (R6)"], out
f = Fake(default=[{"name": "view-profile", "clientRole": True}, {"name": "manage-account", "clientRole": True},
                  {"name": "offline_access", "clientRole": False}])
f.otp |= {"u2"}
f.m["nurse"] = [{"id": "u4", "username": "nurse1"}]
assert ra.findings(f) == ["default role is ['client:manage-account', 'client:view-profile', 'offline_access'], "
                          "expected exactly [client:view-profile]"], ra.findings(f)
clean = Fake()
clean.otp |= {"u2"}
clean.m["nurse"] = [{"id": "u4", "username": "nurse1"}]
assert ra.findings(clean) == []
t = Fake()
t.otp |= {"u2"}
t.m["nurse"] = [{"id": "u4", "username": "nurse1"}]
t.users = [{"id": "t1", "username": "oldslip", "enabled": True, "requiredActions": ["UPDATE_PASSWORD"]},
           {"id": "t2", "username": "newslip", "enabled": True, "requiredActions": ["UPDATE_PASSWORD"]},
           {"id": "u4", "username": "nurse1", "enabled": True, "requiredActions": []}]
now = 8 * 86_400_000
assert ra.findings(t, now_ms=now) == ["oldslip: temporary password is 8 days old (--disable-expired)"], ra.findings(t, now_ms=now)
assert t.puts == []
assert ra.findings(t, now_ms=now, disable=True) == ["oldslip: temporary password is 8 days old - disabled"]
assert t.puts == [("/users/t1", {"enabled": False})], t.puts
print("realm-audit: OK")
