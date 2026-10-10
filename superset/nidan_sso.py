"""Rules for Superset central login (backlog KC-39). Pure Python, unit-tested in tests/test_sso.py; wired in
superset_config.py through NidanSecurityManager only when SUPERSET_SSO_ENABLED / NIDAN_SSO_ENABLED is true.

Claims come from Keycloak's userinfo (realm client "superset"): preferred_username, given_name, family_name,
email, roles (the user's "superset" client roles = Superset role names), groups (Keycloak group paths).
"""

SUPERUSER_ROLES = frozenset({"Admin", "Alpha", "sql_lab"})
SUPERUSERS_PATH = "/app-superusers"


def _strings(value):
    if isinstance(value, str):
        return [value]
    return [v for v in (value or []) if isinstance(v, str)]


def role_keys(claims):
    """Superset role names from the token; superuser roles only for members of /app-superusers (KC-57)."""
    superuser = SUPERUSERS_PATH in _strings(claims.get("groups"))
    return sorted({r for r in _strings(claims.get("roles")) if superuser or r not in SUPERUSER_ROLES})


def userinfo(claims):
    """The dict FAB's auth_user_oauth expects; role_keys feed AUTH_ROLES_MAPPING."""
    return {
        "username": (claims.get("preferred_username") or "").strip().lower(),
        "first_name": claims.get("given_name", ""),
        "last_name": claims.get("family_name", ""),
        "email": claims.get("email", ""),
        "role_keys": role_keys(claims),
    }


def has_superset_role(keys, mapping):
    """True when the token gives at least one Superset role. Without one the person would only be "Public" (no
    permissions) and Superset bounces between its home page and login forever (found live), so sign-in is refused."""
    return any(k in mapping for k in keys or [])


def is_local_account(user, check_password_hash):
    """True for an account with a real password (created locally, e.g. the bootstrap admin). FAB stores the
    hash of "" for users it registers through OAuth, so anything else must never be taken over by a
    Keycloak user of the same name (KC-39-03)."""
    return bool(user and user.password and not check_password_hash(user.password, ""))
