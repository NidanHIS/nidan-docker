#!/bin/bash
set -e

# Set default values if not provided
: "${DB_PASSWORD:=${OPENELIS_DB_PASSWORD:-changeme}}"
: "${DB_SUPERUSER_PASSWORD:=${OPENELIS_DB_ROOT_PASSWORD:-${OPENELIS_DB_PASSWORD:-changeme}}}"

echo "Creating database users..."

# Substitute environment variables in SQL template
envsubst < /docker-entrypoint-initdb.d/1-pgsqlPermissions.sql.template > /tmp/1-pgsqlPermissions.sql

# Execute the generated SQL (only creates users, database will be created in next script)
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/1-pgsqlPermissions.sql

echo "Database users created successfully"
