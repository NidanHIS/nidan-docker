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
THEME_DEFAULT = {
    "algorithm": "default",  # light
    # Brand logo via the THEME path (not the legacy APP_ICON fallback): only this
    # path applies a height (brandLogoHeight) to the navbar logo. The APP_ICON
    # fallback renders the raw image at natural size, which blows out the layout.
    # brandLogoUrl must reach the frontend as "/static/..." (leading slash): the
    # client's URL helper prepends the app root ONLY to leading-slash paths
    # (→ /superset/static/...); a relative "static/..." is left alone and resolves
    # against the current page (→ /superset/dashboard/list/static/... 404).
    # It stays "/static/..." (not double-prefixed to /superset/static/...) because
    # APP_ICON below is kept off "/static/", which makes Superset skip its
    # server-side theme-token prefixing — see the APP_ICON note.
    "token": {
        "brandLogoUrl": "/static/assets/images/nidan-logo.webp",
        "brandLogoAlt": "NidanHIS",
        "brandLogoHref": "/",
        "brandLogoHeight": "30px",
        "brandLogoMargin": "8px 0",
    },
}
THEME_DARK = {}                            # disables dark mode + OS-preference switching (None crashes sub-path deploys)
ENABLE_UI_THEME_ADMINISTRATION = False     # admins can't switch the system theme away from light

# ---------------------------------------------------------------------------
# Branding (rebrandable) — logo + favicon are baked into the image's static dir
# by the Dockerfile (COPY nidan-logo.webp / favicon.ico). To rebrand for a
# hospital: replace nidan-docker/superset/nidan-logo.webp and favicon.ico, then
# rebuild the Superset image.
#   - APP_ICON paths starting with "/static/" are auto-prefixed with the app
#     root (→ /superset/static/...) by Superset, so keep it relative here.
#   - FAVICONS hrefs get the static-assets prefix prepended in spa.html, so they
#     also stay relative here (don't add /superset).
# ---------------------------------------------------------------------------
import mimetypes
mimetypes.add_type("image/webp", ".webp")  # base image's mimetypes doesn't know .webp → would serve as octet-stream

APP_NAME = "NidanHIS"
# Keep APP_ICON already app-root-prefixed (NOT starting with "/static/"). When
# APP_ICON starts with "/static/", Superset auto-prepends the app root to APP_ICON
# AND to every theme brandLogoUrl/brandLogoHref — which then get prefixed a second
# time on the client (→ /superset/superset/... 404). Pre-prefixing APP_ICON makes
# Superset skip that whole block, so the theme tokens above stay single-prefixed.
APP_ICON = APPLICATION_ROOT + "/static/assets/images/nidan-logo.webp"
FAVICONS = [{"href": "/static/assets/images/nidan-favicon.ico", "type": "image/x-icon"}]

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
# JS loader — reads a JS file from the same directory as this config and wraps
# it in a <script> tag.
# ---------------------------------------------------------------------------
def _load_js(name: str, script_id: str) -> str:
    """Read *name* (relative to this file's directory) and wrap in a <script> tag."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    return f'<script id="{script_id}">\n{content}\n</script>\n'

_AUTOSCROLL_JS = _load_js("scroller.js", "__nidan_scroller__")


def inject_scroller_js(response):
    if "text/html" not in (response.content_type or ""):
        return response
    # Superset 6 serves dashboard HTML (and static-file 404 pages) as a streamed
    # response with direct_passthrough set. get_data() on one raises RuntimeError
    # ("Attempted implicit sequence conversion"), which the error handler then hits
    # again while rendering its own page — turning every dashboard into a 500.
    # Nothing to inject into a stream we are not allowed to buffer: leave it alone.
    if getattr(response, "direct_passthrough", False):
        return response
    body = response.get_data(as_text=True)
    if "</body>" not in body:
        return response

    from flask import request, g
    nonce = getattr(request, "csp_nonce", None) or \
            getattr(request, "_csp_nonce", None) or \
            getattr(g, "csp_nonce", None)
    nonce_attr = f' nonce="{nonce}"' if nonce else ""

    combined = ""

    if "__nidan_scroller__" not in body:
        combined += _AUTOSCROLL_JS.replace(
            '<script id="__nidan_scroller__">',
            f'<script id="__nidan_scroller__"{nonce_attr}>', 1
        )

    if combined:
        response.set_data(body.replace("</body>", combined + "</body>", 1))
    return response


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

    # "icon" = brand logo (APP_ICON): the frontend prepends application_root to
    # brand.icon too, so it must be shipped relative or it becomes
    # /superset/superset/static/...nidan-logo.webp (404).
    url_keys = {"url", "path", "icon", "user_logout_url", "user_login_url", "user_info_url"}
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

    # Register the scroller for every HTML response
    app.after_request(inject_scroller_js)