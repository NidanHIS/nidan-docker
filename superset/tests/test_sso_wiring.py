"""superset_config.py central-login wiring (backlog KC-39-01/03/04): `python3 -I superset/tests/test_sso_wiring.py`.

Loads the real config with Flask / FAB / Superset / werkzeug stubbed, then exercises NidanSecurityManager.
"""
import os
import runpy
import secrets
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE.parent / "superset_config.py"
sys.path.insert(0, str(HERE.parent))  # nidan_sso


def stub(name, **attrs):
    mod = sys.modules.setdefault(name, types.ModuleType(name))
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


class BaseSM:
    def auth_user_oauth(self, userinfo):
        return "registered-or-logged-in"


SESSION = {}


class BaseOAuthView:
    appbuilder = types.SimpleNamespace(sm=types.SimpleNamespace(oauth_providers=[{"name": "nidan-keycloak"}]))

    def login(self, provider=None):
        return provider or "login page"


stub("flask", redirect=lambda *a, **k: None, session=SESSION)
stub("flask_appbuilder", expose=lambda *a, **k: (lambda f: f), IndexView=type("IndexView", (), {}))
stub("flask_appbuilder.security")
stub("flask_appbuilder.security.manager", AUTH_OAUTH=4)
stub("flask_appbuilder.security.views", AuthOAuthView=BaseOAuthView)
stub("superset")
stub("superset.security", SupersetSecurityManager=BaseSM)
stub("werkzeug")
stub("werkzeug.security", check_password_hash=lambda hashed, pw: hashed == "h:" + pw)

BASE = {"SUPERSET_SECRET_KEY": secrets.token_urlsafe(48)}
KEYS = ("SUPERSET_SECRET_KEY", "NIDAN_DEPLOYMENT", "SUPERSET_SSO_ENABLED", "NIDAN_SSO_ENABLED", "KEYCLOAK_PUBLIC_URL",
        "KC_SUPERSET_CLIENT_SECRET")


def load(**env):
    for k in KEYS:
        os.environ.pop(k, None)
    os.environ.update(BASE, **env)
    try:
        return runpy.run_path(str(CONFIG))
    except RuntimeError as e:
        return e


class User:
    def __init__(self, password):
        self.password = password


def main():
    off = load()
    assert "CUSTOM_SECURITY_MANAGER" not in off and off.get("AUTH_TYPE") is None, "SSO off must leave auth alone"

    on = dict(SUPERSET_SSO_ENABLED="true", KEYCLOAK_PUBLIC_URL="https://ehr.test:9443/auth",
              KC_SUPERSET_CLIENT_SECRET="x" * 24)
    assert isinstance(load(**dict(on, KC_SUPERSET_CLIENT_SECRET="")), RuntimeError)
    assert isinstance(load(**dict(on, KEYCLOAK_PUBLIC_URL="http://ehr.test/auth")), RuntimeError)
    assert "CUSTOM_SECURITY_MANAGER" in load(NIDAN_SSO_ENABLED="true", KEYCLOAK_PUBLIC_URL=on["KEYCLOAK_PUBLIC_URL"],
                                             KC_SUPERSET_CLIENT_SECRET=on["KC_SUPERSET_CLIENT_SECRET"])

    cfg = load(**on)
    assert cfg["AUTH_TYPE"] == 4 and cfg["AUTH_ROLES_SYNC_AT_LOGIN"] is True
    app = cfg["OAUTH_PROVIDERS"][0]["remote_app"]
    assert app["issuer"] == "https://ehr.test:9443/auth/realms/nidan"
    assert app["access_token_url"].startswith("http://gateway:8099/") and app["jwks_uri"].startswith("http://gateway:8099/")
    assert app["client_kwargs"]["code_challenge_method"] == "S256"

    sm = object.__new__(cfg["CUSTOM_SECURITY_MANAGER"])
    # KC-39-04: no password login at all (FAB's REST login calls auth_user_db for provider "db")
    assert sm.auth_user_db("admin", "anything") is None and sm.auth_user_db("asha", "") is None
    # KC-39-03: a local account is never taken over; an OAuth-created one (hash of "") is fine
    accounts = {"admin": User("h:real-admin-password"), "asha": User("h:")}
    sm.find_user = lambda username=None: accounts.get(username)
    assert sm.auth_user_oauth({"username": "admin"}) is None
    assert sm.auth_user_oauth({"username": "asha", "role_keys": ["Gamma"]}) == "registered-or-logged-in"
    assert sm.auth_user_oauth({"username": "new-user", "role_keys": ["Clinical"]}) == "registered-or-logged-in"
    # no Superset role in the token: refused, not logged in as a permission-less Public user (redirect loop)
    assert sm.auth_user_oauth({"username": "new-user", "role_keys": []}) is None
    # userinfo comes only from verified ID token claims; superuser roles need /app-superusers
    info = sm.get_oauth_user_info("nidan-keycloak", {"userinfo": {"sub": "s1", "preferred_username": "Asha",
                                                                 "roles": ["Gamma", "Admin"], "groups": ["/staff/Doctor"]}})
    assert info["username"] == "asha" and info["role_keys"] == ["Gamma"], info
    assert sm.get_oauth_user_info("nidan-keycloak", {"access_token": "x"}) == {}
    # one provider: /login/ goes straight to Keycloak; after a refused sign-in (flashed message) the page shows
    assert cfg["CUSTOM_SECURITY_MANAGER"].register_superset_auth_view is False, "Superset's /login/ would shadow ours"
    view = cfg["CUSTOM_SECURITY_MANAGER"].authoauthview()
    assert view.login() == "nidan-keycloak", "login page must go straight to the one provider"
    assert view.login("nidan-keycloak") == "nidan-keycloak"
    SESSION["_flashes"] = [("warning", "denied")]
    assert view.login() == "login page", "a failed sign-in must show the page, not loop"
    SESSION.clear()
    print("superset central-login wiring: OK")


if __name__ == "__main__":
    main()
