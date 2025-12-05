#!/bin/sh
set -e

create_role_and_db() {
  role_name="$1"
  role_password="$2"
  db_name="$3"

  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    DO \$\$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${role_name}') THEN
        EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', '${role_name}', '${role_password}');
      END IF;
    END
    \$\$;

    DO \$\$
    BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${db_name}') THEN
        EXECUTE format('CREATE DATABASE %I OWNER %I TEMPLATE template0 ENCODING ''UTF8''', '${db_name}', '${role_name}');
      END IF;
    END
    \$\$;
EOSQL
}

create_role_and_db "${OPENMRS_DB_USER}" "${OPENMRS_DB_PASSWORD}" "${OPENMRS_DB_NAME}"
create_role_and_db "${OPENELIS_DB_USER}" "${OPENELIS_DB_PASSWORD}" "${OPENELIS_DB_NAME}"
create_role_and_db "${ODOO_DB_USER}" "${ODOO_DB_PASSWORD}" "${ODOO_DB_NAME}"
create_role_and_db "${BAHMNI_REPORTS_DB_USER}" "${BAHMNI_REPORTS_DB_PASSWORD}" "${BAHMNI_REPORTS_DB_NAME}"

