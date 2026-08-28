FROM mariadb:10.11
# /docker-entrypoint-initdb.d runs in ASCII order and digits sort before
# letters, so the schema is numbered too. Without that the debezium grant would
# run before the openmrs schema existed.
COPY db_backup/openmrsdb_backup.sql /docker-entrypoint-initdb.d/01-openmrs.sql
# ST-10.10.2. The template sits outside initdb.d so the entrypoint cannot run it
# with the password token unsubstituted; 10-debezium-user.sh renders and pipes it.
COPY db_init/debezium-user.sql.tmpl /etc/nidan/debezium-user.sql.tmpl
COPY db_init/10-debezium-user.sh    /docker-entrypoint-initdb.d/10-debezium-user.sh
