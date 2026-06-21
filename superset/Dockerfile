# Extend Apache Superset with DB drivers: PostgreSQL + MySQL
# - psycopg2: OpenMRS/OpenELIS/Odoo when using PostgreSQL
# - pymysql: OpenMRS or other sources using mysql+pymysql://
FROM apache/superset:latest

USER root

RUN uv pip install --no-cache-dir psycopg2-binary pymysql

# Brand assets (rebrandable): drop replacement files in this folder + rebuild.
# Referenced by APP_ICON / FAVICONS in superset_config.py. Served under
# /superset/static/assets/images/ (the app-root prefix is added automatically).
COPY nidan-logo.webp /app/superset/static/assets/images/nidan-logo.webp
COPY favicon.ico     /app/superset/static/assets/images/nidan-favicon.ico

USER superset
