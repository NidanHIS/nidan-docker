# Superset Configuration for Path-Based Deployment
import os

# Use PyMySQL for any mysql:// SQLAlchemy URIs (avoids requiring mysqlclient / MySQLdb C extension)
try:
    import pymysql

    pymysql.install_as_MySQLdb()
except ImportError:
    pass

from flask import redirect
from flask_appbuilder import expose, IndexView

# Secret key for session encryption
SECRET_KEY = os.environ.get('SUPERSET_SECRET_KEY', 'your-secret-key-here')

# SQLAlchemy database URI
SQLALCHEMY_DATABASE_URI = 'sqlite:////app/superset_home/superset.db'

# Flask App Configuration
# CRITICAL: Tell Superset it's behind /superset/ prefix
ENABLE_PROXY_FIX = True

# Application root for URL generation (no trailing slash to avoid double prefix in redirects)
APPLICATION_ROOT = '/superset'

# Static assets must use the same prefix so CSS/JS load correctly behind proxy
STATIC_ASSETS_PREFIX = '/superset'

# Fix health check path issue
SERVER_NAME = None

# WTF CSRF settings
WTF_CSRF_ENABLED = True
WTF_CSRF_EXEMPT_LIST = []
WTF_CSRF_TIME_LIMIT = None
WTF_CSRF_SSL_STRICT = False
WTF_CSRF_CHECK_DEFAULT = False

# Set the authentication type
# AUTH_TYPE = AUTH_DB  # Database authentication (default)

# Uncomment to setup Full admin role name
# AUTH_ROLE_ADMIN = 'Admin'

# Uncomment to setup Public role name, no authentication needed
# AUTH_ROLE_PUBLIC = 'Public'

# Will allow user self registration
# AUTH_USER_REGISTRATION = True

# The default user self registration role
# AUTH_USER_REGISTRATION_ROLE = "Public"

# CORS settings (if needed for API access)
ENABLE_CORS = True
CORS_OPTIONS = {
    'supports_credentials': True,
    'allow_headers': ['*'],
    'origins': ['*']
}

# Session configuration
SESSION_COOKIE_NAME = 'superset_session'
SESSION_COOKIE_PATH = '/superset'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = False  # Set to True if using HTTPS
SESSION_COOKIE_SAMESITE = 'Lax'

# Enable all visualization types and chart plugins
DEFAULT_VIZ_TYPE = "table"

# Enable ECharts visualization plugins and other features
FEATURE_FLAGS = {
    "ENABLE_TEMPLATE_PROCESSING": True,
    "ENABLE_CHART_FLUX_FILTERS": True,
    "DASHBOARD_CACHE": True,
    "ENABLE_EMBEDDED_SUPERSET": True,
    "ENABLE_EXPLORE_JSON": True,
    "ENABLE_CUSTOM_FORM_DATA": True,
    "KV_STORE": True,
    "ENABLE_PERMISSION_V2": True,
    # Per-dashboard role-based access control (hospital RBAC: lab/pharmacy/etc.)
    "DASHBOARD_RBAC": True,
}

# ---------------------------------------------------------------------------
# Theme — force LIGHT mode for the hospital setting (no dark toggle).
# Superset 6: an empty dark theme forces a single (light) theme for all.
# NOTE: do NOT use THEME_DARK = None here. When deployed under a sub-path
# (APPLICATION_ROOT = /superset), superset/app.py iterates THEME_DEFAULT and
# THEME_DARK and calls .get("token") on each — None crashes app creation
# ("'NoneType' object has no attribute 'get'"). An empty dict {} is treated
# identically to None by get_theme_bootstrap_data/_process_theme (both yield
# no custom dark theme → light only) but survives the app.py token loop.
# ---------------------------------------------------------------------------
THEME_DEFAULT = {"algorithm": "default"}  # light
THEME_DARK = {}                            # disables dark mode + OS-preference switching (None crashes sub-path deploys)
ENABLE_UI_THEME_ADMINISTRATION = False     # admins can't switch the system theme away from light

# ---------------------------------------------------------------------------
# Role-based access
# ---------------------------------------------------------------------------
# Anonymous (not-logged-in) visitors get the Public role, used for the public
# dashboards / embedded monitor screens. PUBLIC_ROLE_LIKE grants Public the
# base perms needed to render a dashboard; DASHBOARD_RBAC then restricts which
# dashboards Public can actually see (only those with the Public role attached).
AUTH_ROLE_PUBLIC = "Public"
PUBLIC_ROLE_LIKE = "Gamma"

# Enable all built-in chart types
DEFAULT_VIZ_TYPE = "table"

# Enable all visualization types by removing restrictions
# This ensures all built-in chart types are available
ENABLE_CUSTOM_FORM_DATA = True
KV_STORE = True

# Chart type configuration - ensure basic charts are available
# The bar chart should be available by default in Superset

# Database connection configuration
SQLALCHEMY_TRACK_MODIFICATIONS = False

# SQLite doesn't support connection pooling, so only set engine options for non-SQLite databases
if not SQLALCHEMY_DATABASE_URI.startswith('sqlite'):
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": 10,
        "pool_recycle": 3600,
        "pool_pre_ping": True,
    }

# Configure logging
LOGGER_LEVEL = 'INFO'

# Prevent Superset from redirecting to root
PREFERRED_URL_SCHEME = 'http'  # Change to 'https' in production

# Additional configuration for reverse proxy
ENABLE_PROXY_FIX = True
PROXY_FIX_CONFIG = {
    "x_for": 1,
    "x_proto": 1,
    "x_host": 1,
    "x_port": 1,
    "x_prefix": 1,
}

# Custom post-login redirect - avoid broken /superset/welcome/ (404), use dashboard list
class SupersetIndexView(IndexView):
    @expose("/")
    def index(self):
        from flask import g
        if hasattr(g, "user") and g.user and g.user.is_authenticated:
            return redirect("/superset/dashboard/list/")
        return redirect("/superset/login/")


FAB_INDEX_VIEW = "superset_config.SupersetIndexView"


# ---------------------------------------------------------------------------
# Sub-path nav fix (logout/login/menu links double-prefixed → /superset/superset/…)
# ---------------------------------------------------------------------------
# Under APPLICATION_ROOT=/superset, the backend builds every menu_data URL with
# url_for(), which already includes the app-root prefix (e.g. "/superset/logout/",
# "/superset/dashboard/list/"). The O3 frontend then prepends `application_root`
# AGAIN via a non-idempotent helper, yielding "/superset/superset/logout/" etc.
# (user_info_url works only because upstream ships it relative — "/user_info/".)
#
# The frontend contract is "menu URLs are relative; the client adds the root".
# So strip exactly ONE leading app-root from menu_data URLs and let the frontend
# re-add it once. Stripping a single leading occurrence is correct even for
# Superset-blueprint routes (url_for("Superset.welcome") = app_root + "/superset/
# welcome/" → strip → "/superset/welcome/" → frontend → "/superset/superset/
# welcome/"), and is a no-op for already-relative URLs ("/user_info/").
def FLASK_APP_MUTATOR(app):  # noqa: N802 (Superset config hook name)
    from superset.views import base as superset_base

    app_root = (app.config.get("APPLICATION_ROOT") or "/").rstrip("/")
    if not app_root:
        return  # served at "/", nothing to strip

    url_keys = {"url", "path", "user_logout_url", "user_login_url", "user_info_url"}
    prefix = app_root + "/"

    def strip_root(obj):
        if isinstance(obj, dict):
            return {
                k: (v[len(app_root):] if k in url_keys and isinstance(v, str)
                    and v.startswith(prefix) else strip_root(v))
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [strip_root(v) for v in obj]
        return obj

    original_menu_data = superset_base.menu_data
    superset_base.menu_data = lambda user: strip_root(original_menu_data(user))