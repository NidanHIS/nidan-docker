"""Fail-closed checks of superset_config.py (backlog KC-03-05/06/07/08). Run: python3 -I superset/tests/test_config_guards.py

Loads the real config with Flask/FAB stubbed out, under different environments, and asserts it
refuses unset/short/known/committed secret keys and ships CORS off, CSRF defaults, Secure cookie,
no Public permissions and embedding only with a guest secret.
"""
import os
import runpy
import secrets
import sys
import types
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "superset_config.py"
ENV = ("SUPERSET_SECRET_KEY", "NIDAN_DEPLOYMENT", "SUPERSET_GUEST_TOKEN_SECRET", "SUPERSET_COOKIE_SECURE")

for name in ("flask", "flask_appbuilder"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["flask"].redirect = lambda *a, **k: None
sys.modules["flask_appbuilder"].expose = lambda *a, **k: (lambda f: f)
sys.modules["flask_appbuilder"].IndexView = type("IndexView", (), {})


def load(env):
    for k in ENV:
        os.environ.pop(k, None)
    os.environ.update(env)
    try:
        return runpy.run_path(str(CONFIG))
    except RuntimeError as e:
        return e


def main():
    strong = secrets.token_urlsafe(48)
    for bad in ({}, {"SUPERSET_SECRET_KEY": "your-secret-key-here"}, {"SUPERSET_SECRET_KEY": "short"}):
        assert isinstance(load(bad), RuntimeError), bad

    # The key committed in the tracked demo .env: accepted for the demo, refused in production.
    demo = next(line.split("=", 1)[1].strip() for line in (CONFIG.parent.parent / ".env").read_text().splitlines()
                if line.startswith("SUPERSET_SECRET_KEY="))
    assert isinstance(load({"SUPERSET_SECRET_KEY": demo}), dict)
    assert isinstance(load({"SUPERSET_SECRET_KEY": demo, "NIDAN_DEPLOYMENT": "production"}), RuntimeError)

    g = load({"SUPERSET_SECRET_KEY": strong, "NIDAN_DEPLOYMENT": "production"})
    assert isinstance(g, dict)
    assert g["ENABLE_CORS"] is False
    assert g["PUBLIC_ROLE_LIKE"] is None
    assert g["SESSION_COOKIE_SECURE"] is True and g["SESSION_COOKIE_HTTPONLY"] is True
    assert g["SESSION_COOKIE_NAME"] == "superset_session" and g["SESSION_COOKIE_PATH"] == "/superset"
    assert "WTF_CSRF_CHECK_DEFAULT" not in g and g["WTF_CSRF_ENABLED"] is True
    assert g["FEATURE_FLAGS"]["ENABLE_EMBEDDED_SUPERSET"] is False

    g = load({"SUPERSET_SECRET_KEY": strong, "SUPERSET_GUEST_TOKEN_SECRET": secrets.token_urlsafe(40)})
    assert g["FEATURE_FLAGS"]["ENABLE_EMBEDDED_SUPERSET"] is True
    print("superset config guards: OK")


if __name__ == "__main__":
    main()
