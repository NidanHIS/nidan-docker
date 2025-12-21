#!/bin/bash
set -e

echo "Running siteInfo.sql..."

# Run siteInfo.sql against the clinlims database
if [ -f /docker-entrypoint-initdb.d/siteInfo.sql ]; then
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "clinlims" -f /docker-entrypoint-initdb.d/siteInfo.sql
    echo "siteInfo.sql completed"
else
    echo "siteInfo.sql not found, skipping"
fi

