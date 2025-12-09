#!/bin/bash
set -e

# Wait for PostgreSQL to be ready
# Wait for PostgreSQL to be ready
until PGPASSWORD="${POSTGRES_PASSWORD}" psql -U "${POSTGRES_USER}" -d postgres -c '\q' 2>/dev/null; do
  >&2 echo "PostgreSQL is unavailable - sleeping"
  sleep 1
done

# Create database user if it doesn't exist
PGPASSWORD="${POSTGRES_PASSWORD}" psql -U "${POSTGRES_USER}" -d postgres <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_user WHERE usename = '${ODOO_DB_USER}') THEN
            CREATE USER ${ODOO_DB_USER} WITH PASSWORD '${ODOO_DB_PASSWORD}';
        END IF;
    END
    \$\$;
EOSQL

# Create database if it doesn't exist
PGPASSWORD="${POSTGRES_PASSWORD}" psql -U "${POSTGRES_USER}" -d postgres <<-EOSQL
    SELECT 'CREATE DATABASE ${ODOO_DB_NAME} OWNER ${ODOO_DB_USER}'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${ODOO_DB_NAME}')\gexec
EOSQL

# Grant privileges
PGPASSWORD="${POSTGRES_PASSWORD}" psql -U "${POSTGRES_USER}" -d postgres <<-EOSQL
    GRANT ALL PRIVILEGES ON DATABASE ${ODOO_DB_NAME} TO ${ODOO_DB_USER};
    ALTER DATABASE ${ODOO_DB_NAME} OWNER TO ${ODOO_DB_USER};
EOSQL

echo "Database ${ODOO_DB_NAME} and user ${ODOO_DB_USER} are ready"

