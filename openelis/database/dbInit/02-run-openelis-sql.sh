#!/bin/bash
set -e

echo "Running OpenELIS-Global.sql to create schema and tables..."

# Check if database exists first
if ! psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -tc "SELECT 1 FROM pg_database WHERE datname = 'clinlims'" | grep -q 1; then
    echo "ERROR: clinlims database does not exist. Please ensure 01-init-user-db.sh ran successfully."
    exit 1
fi

# The OpenELIS-Global.sql file creates the schema and all tables
# Run it against the clinlims database
# Use ON_ERROR_STOP=0 to continue on errors (some objects may already exist)
psql -v ON_ERROR_STOP=0 --username "$POSTGRES_USER" --dbname "clinlims" -f /docker-entrypoint-initdb.d/OpenELIS-Global.sql > /tmp/openelis-init.log 2>&1

# Check if critical errors occurred (ignore "already exists" messages)
if grep -i "error\|fatal" /tmp/openelis-init.log | grep -v "already exists" | grep -v "does not exist"; then
    echo "WARNING: Some errors occurred during OpenELIS-Global.sql execution. Check logs above."
else
    echo "OpenELIS-Global.sql completed successfully"
fi

