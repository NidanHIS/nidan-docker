#!/bin/bash
set -e

echo "Creating clinlims user and database..."

# Create user and database
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- Create user if doesn't exist
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_user WHERE usename = 'clinlims') THEN
            CREATE USER clinlims WITH PASSWORD '${POSTGRES_PASSWORD}';
        END IF;
    END
    \$\$;

    -- Create database if doesn't exist
    SELECT 'CREATE DATABASE clinlims OWNER clinlims'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'clinlims')\gexec

    -- Grant all privileges
    GRANT ALL PRIVILEGES ON DATABASE clinlims TO clinlims;
EOSQL

echo "clinlims user and database created successfully"