"""Plain tests for superset/nidan_sso.py (backlog KC-39-03/04 rules): `python3 -I superset/tests/test_sso.py`."""
import importlib.util
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("nidan_sso", Path(__file__).resolve().parent.parent / "nidan_sso.py")
ns = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ns)


def fake_hash(password):
    return "h:" + password


def fake_check(hashed, password):
    return hashed == "h:" + password


class _User:
    def __init__(self, password):
        self.password = password


class Rules(unittest.TestCase):
    def test_superuser_roles_need_app_superusers(self):
        claims = {"roles": ["Gamma", "Admin", "sql_lab", "Clinical"], "groups": ["/staff/Doctor"]}
        self.assertEqual(ns.role_keys(claims), ["Clinical", "Gamma"])
        claims["groups"] = ["/app-superusers"]
        self.assertEqual(ns.role_keys(claims), ["Admin", "Clinical", "Gamma", "sql_lab"])

    def test_lookalike_paths_and_junk(self):
        for path in ("/staff/app-superusers", "app-superusers", "/app-superusers/x"):
            self.assertNotIn("Admin", ns.role_keys({"roles": ["Admin"], "groups": [path]}), path)
        self.assertEqual(ns.role_keys({"roles": "Gamma", "groups": None}), ["Gamma"])
        self.assertEqual(ns.role_keys({"roles": [1, None]}), [])

    def test_userinfo_shape(self):
        info = ns.userinfo({"preferred_username": " Asha ", "given_name": "Asha", "roles": ["Gamma"]})
        self.assertEqual(info["username"], "asha")
        self.assertEqual(info["role_keys"], ["Gamma"])

    def test_local_account_is_never_taken_over(self):
        self.assertTrue(ns.is_local_account(_User(fake_hash("admin-real-pw")), fake_check))
        self.assertFalse(ns.is_local_account(_User(fake_hash("")), fake_check))  # created by OAuth
        self.assertFalse(ns.is_local_account(None, fake_check))



class SupersetRole(unittest.TestCase):
    MAPPING = {"Gamma": ["Gamma"], "Clinical": ["Clinical"]}

    def test_a_mapped_role_is_needed(self):
        self.assertTrue(ns.has_superset_role(["Clinical"], self.MAPPING))
        self.assertFalse(ns.has_superset_role([], self.MAPPING))
        self.assertFalse(ns.has_superset_role(None, self.MAPPING))
        self.assertFalse(ns.has_superset_role(["Public", "unknown"], self.MAPPING))

if __name__ == "__main__":
    unittest.main()
