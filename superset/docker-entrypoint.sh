#!/bin/sh
set -e

echo "Running database migrations..."
superset db upgrade

# Fail closed on the bootstrap admin password (it becomes a working login on a fresh install).
case "${SUPERSET_ADMIN_PASSWORD:-}" in
  ""|admin|CHANGEME_GENERATE_ME)
    echo "SUPERSET_ADMIN_PASSWORD is unset or a known default; refusing to start" >&2; exit 1 ;;
esac
if [ "${NIDAN_DEPLOYMENT:-}" = "production" ] && [ "${#SUPERSET_ADMIN_PASSWORD}" -lt 12 ]; then
  echo "SUPERSET_ADMIN_PASSWORD must be at least 12 characters when NIDAN_DEPLOYMENT=production" >&2; exit 1
fi

echo "Creating admin user (if not exists)..."
superset fab create-admin \
  --username "${SUPERSET_ADMIN_USERNAME:-admin}" \
  --firstname "${SUPERSET_ADMIN_FIRSTNAME:-Superset}" \
  --lastname "${SUPERSET_ADMIN_LASTNAME:-Admin}" \
  --email "${SUPERSET_ADMIN_EMAIL:-admin@superset.local}" \
  --password "${SUPERSET_ADMIN_PASSWORD}" \
  || true

echo "Initializing Superset..."
superset init

echo "Initializing metadata (databases, datasets, charts, dashboard)..."
python3 /app/pythonpath/init_superset_metadata.py || true

echo "Starting Gunicorn..."
exec gunicorn \
  --bind 0.0.0.0:8088 \
  --workers 4 \
  --timeout 120 \
  --limit-request-line 0 \
  --limit-request-field_size 0 \
  "superset.app:create_app()"
