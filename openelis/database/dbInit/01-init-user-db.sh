#!/bin/bash
set -e

echo "Creating clinlims database..."

# Create database if it doesn't exist
# Note: The OpenELIS-Global.sql file will create the schema and tables
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- Create database if doesn't exist
    SELECT 'CREATE DATABASE clinlims OWNER clinlims'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'clinlims')\gexec

    -- Grant all privileges (will fail silently if already granted)
    GRANT ALL PRIVILEGES ON DATABASE clinlims TO clinlims;
    
    -- Set database owner (will fail silently if already set)
    ALTER DATABASE clinlims OWNER TO clinlims;
EOSQL

# Verify database was created
if psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -tc "SELECT 1 FROM pg_database WHERE datname = 'clinlims'" | grep -q 1; then
    echo "clinlims database created successfully"
else
    echo "ERROR: Failed to create clinlims database"
    exit 1
fi