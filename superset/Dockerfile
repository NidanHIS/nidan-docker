# Extend Apache Superset with DB drivers: PostgreSQL + MySQL
# - psycopg2: OpenMRS/OpenELIS/Odoo when using PostgreSQL
# - pymysql: OpenMRS or other sources using mysql+pymysql://
FROM apache/superset:latest

USER root

# authlib: Flask-AppBuilder's OAuth login (central login, KC-39). Without it Superset with SUPERSET_SSO_ENABLED=true
# stops at start with "No module named 'authlib'" (found on the harness, 2026-10-10). Pinned: the version KC-39 was
# checked against.
RUN uv pip install --no-cache-dir psycopg2-binary pymysql authlib==1.3.2

# Brand assets (rebrandable): drop replacement files in this folder + rebuild.
# Referenced by APP_ICON / FAVICONS in superset_config.py. Served under
# /superset/static/assets/images/ (the app-root prefix is added automatically).
COPY nidan-logo.webp /app/superset/static/assets/images/nidan-logo.webp
COPY favicon.ico     /app/superset/static/assets/images/nidan-favicon.ico

USER superset
