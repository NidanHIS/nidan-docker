#!/bin/bash
# ST-10.10.2 — create the least-privilege Debezium account on a fresh volume.
#
# The SQL lives in debezium-user.sql.tmpl so it stays reviewable in version
# control, and carries a __DEBEZIUM_PASSWORD__ token rather than a credential:
# everything baked into an image layer is readable by anyone who can pull it.
#
# The template is rendered to stdout and piped straight to the client. It is
# deliberately NOT written back to disk — /docker-entrypoint-initdb.d is not
# writable by the mysql user, and more importantly a rendered file would leave
# the password sitting in the container filesystem. It also lives outside
# initdb.d so the entrypoint cannot execute it un-substituted.
set -euo pipefail

TMPL=/etc/nidan/debezium-user.sql.tmpl

: "${DEBEZIUM_DB_PASSWORD:?refusing to initialise: DEBEZIUM_DB_PASSWORD is unset or still the template placeholder}"

case "$DEBEZIUM_DB_PASSWORD" in
  *CHANGEME*)
    echo "refusing to initialise: DEBEZIUM_DB_PASSWORD is unset or still the template placeholder" >&2
    exit 78
    ;;
esac

# Substitution is done by the client's stdin, so the credential never touches
# the filesystem and never appears in a process argument list.
sed "s|__DEBEZIUM_PASSWORD__|${DEBEZIUM_DB_PASSWORD}|g" "$TMPL" \
  | mariadb --protocol=socket -uroot -p"${MARIADB_ROOT_PASSWORD:-${MYSQL_ROOT_PASSWORD}}"

echo "debezium account created with REPLICATION SLAVE, REPLICATION CLIENT and SELECT on openmrs"
