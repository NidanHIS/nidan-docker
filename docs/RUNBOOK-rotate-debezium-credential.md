# Runbook — rotate the Debezium database credential

**Applies to:** an existing deployment already running with `DEBEZIUM_DB_USER=root`.
**Story:** ST-10.10.3 · **Depends on:** ST-10.10.1, ST-10.10.2
**Time:** about 10 minutes, of which roughly 60–90 seconds is CDC downtime.

A fresh install needs none of this: `openmrs-db/db_init/` creates the account on
first boot. This is only for stacks whose database volume already exists, where
`/docker-entrypoint-initdb.d` will never run again.

---

## What you are changing, and why

Debezium authenticates to the clinical database as `root`. Root can read every
schema, write every row, and grant itself more. The CDC connector needs none of
that — it needs to read the binlog and SELECT from `openmrs`. Anyone who obtains
the CIS credential today obtains full control of the patient database.

After this runbook, that credential is a `debezium` account that cannot write.

---

## Downtime, stated honestly

| Service | Restarts | Impact |
|---|---|---|
| `openmrs-db` | **no** | none — the account is added online |
| `nidan-cis` | **yes**, once | CDC pauses for its startup, roughly 60–90 seconds |
| `openmrs-backend`, `odoo`, `openelis`, gateway | no | none |

**No events are lost.** CIS stores Debezium offsets with
`JdbcOffsetBackingStore` in the integration Postgres, not in the container —
see `nidan-middleware/integration/cis/.../OpenmrsDebeziumEngineRunner.java:168`.
On restart the connector resumes from the stored binlog position, so changes
written to OpenMRS while CIS is down are picked up on the next poll rather than
skipped.

> One caveat worth knowing before you start. `scripts/volume-backup.sh restore`
> replaces `nidan-integration-db-data` wholesale (`rm -rf` then untar). Restoring
> a backup taken *before* this rotation restores the old offsets **and** the old
> `.env`. If you restore, re-run this runbook.

---

## Before you start

```bash
cd nidan-docker
grep -E '^DEBEZIUM_DB_(USER|PASSWORD)=' .env
```
Expected: `DEBEZIUM_DB_USER=root` and a password. If it already says `debezium`,
this runbook has been applied — stop here.

Take a note of the current values. Step 6 rolls back to them.

---

## Step 1 — generate a password

```bash
NEW_DBZ_PW=$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32)
echo "$NEW_DBZ_PW"
```
Expected: 32 alphanumeric characters. Keep this shell open; later steps use it.

*Rollback: none needed, nothing has changed yet.*

---

## Step 2 — create the account on the running database

```bash
docker exec -i nidan-openmrs-db mariadb -uroot -p"$OPENMRS_DB_ROOT_PASSWORD" <<SQL
CREATE USER IF NOT EXISTS 'debezium'@'%' IDENTIFIED BY '${NEW_DBZ_PW}';
GRANT REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO 'debezium'@'%';
GRANT SELECT ON openmrs.* TO 'debezium'@'%';
FLUSH PRIVILEGES;
SQL
```
Expected: no output. Any output is an error.

*Rollback:* `DROP USER 'debezium'@'%';`

---

## Step 3 — verify the grants before you rely on them

```bash
docker exec nidan-openmrs-db mariadb -uroot -p"$OPENMRS_DB_ROOT_PASSWORD" \
  -e "SHOW GRANTS FOR 'debezium'@'%'"
```
Expected, exactly two lines:
```
GRANT REPLICATION SLAVE, BINLOG MONITOR ON *.* TO `debezium`@`%` IDENTIFIED BY PASSWORD '*...'
GRANT SELECT ON `openmrs`.* TO `debezium`@`%`
```
`BINLOG MONITOR` is MariaDB 10.5+'s name for `REPLICATION CLIENT`. Same
privilege — not a deviation.

Confirm it cannot write:
```bash
docker exec nidan-openmrs-db mariadb -udebezium -p"$NEW_DBZ_PW" \
  -e "INSERT INTO openmrs.visit (patient_id) VALUES (99999)"
```
Expected: `ERROR 1142 (42000) ... INSERT command denied to user 'debezium'@'...'`

**If that INSERT succeeds, stop.** The grants are wrong. Roll back step 2 and
do not continue.

---

## Step 4 — point CIS at the new account

```bash
cp .env .env.bak.$(date +%Y%m%d-%H%M%S)      # rollback copy — keep it outside the repo if you can
sed -i.tmp "s/^DEBEZIUM_DB_USER=.*/DEBEZIUM_DB_USER=debezium/" .env
sed -i.tmp "s/^DEBEZIUM_DB_PASSWORD=.*/DEBEZIUM_DB_PASSWORD=${NEW_DBZ_PW}/" .env
rm -f .env.tmp
grep -E '^DEBEZIUM_DB_(USER|PASSWORD)=' .env
```
Expected: user is `debezium`, password is the 32 characters from step 1.

*Rollback:* `cp .env.bak.<timestamp> .env`

---

## Step 5 — restart CIS and confirm CDC resumed

```bash
docker compose up -d --force-recreate nidan-cis
docker compose logs -f nidan-cis
```
Expected within about 90 seconds:

- no line containing `refusing to start:` — that means a placeholder survived in
  `.env`; fix it and repeat
- no `Access denied for user 'debezium'`
- a Debezium line reporting the connector started and the offset it resumed from

Then prove an event still flows end to end:
```bash
docker exec nidan-openmrs-db mariadb -uroot -p"$OPENMRS_DB_ROOT_PASSWORD" \
  -e "UPDATE openmrs.location SET name = name WHERE location_id = 1"
docker exec nidan-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 --topic openmrs.location --from-beginning --max-messages 1 --timeout-ms 15000
```
Expected: one message within 15 seconds.

*Rollback:* restore `.env` from step 4 and `docker compose up -d --force-recreate nidan-cis`.

---

## Step 6 — remove root's ability to serve as the CDC account

Only after step 5 has passed. This is the irreversible-feeling step, and it is
the one that actually closes the exposure.

```bash
docker exec nidan-openmrs-db mariadb -uroot -p"$OPENMRS_DB_ROOT_PASSWORD" \
  -e "SELECT user, host FROM mysql.user WHERE user='root';"
```
Expected: root remains, restricted to `localhost`. Do **not** drop root.

If root is listed with host `%`, it is reachable from any container on
`nidan-network`. Restrict it:
```bash
docker exec nidan-openmrs-db mariadb -uroot -p"$OPENMRS_DB_ROOT_PASSWORD" \
  -e "DROP USER 'root'@'%';"
```
*Rollback:* recreate it with the password from `.env`:
`CREATE USER 'root'@'%' IDENTIFIED BY '<OPENMRS_DB_ROOT_PASSWORD>'; GRANT ALL ON *.* TO 'root'@'%' WITH GRANT OPTION;`

**Verify nothing else depended on root@%** before you run the DROP:
```bash
grep -rn "DB_USER=root\|-uroot" docker-compose.yml dev.docker-compose.yml .env
```
Expected: only local `docker exec` usages, no service configured to connect as
root over the network. If a service is listed, rotate it first.

---

## Step 7 — rotate the old credential as compromised

The previous Debezium password was `root`'s password, and it has been sitting in
`.env`. If that file was ever committed, pushed, screenshotted, or pasted into a
ticket, treat `OPENMRS_DB_ROOT_PASSWORD` as public and rotate it too:

```bash
docker exec nidan-openmrs-db mariadb -uroot -p"$OPENMRS_DB_ROOT_PASSWORD" \
  -e "ALTER USER 'root'@'localhost' IDENTIFIED BY '<new>';"
```
Then update `OPENMRS_DB_ROOT_PASSWORD` in `.env`. No service restart is needed —
nothing connects as root over the network once step 6 is done.

---

## Done when

- [ ] `SHOW GRANTS FOR 'debezium'@'%'` returns exactly the two expected lines
- [ ] an INSERT as `debezium` is denied with error 1142
- [ ] `.env` has `DEBEZIUM_DB_USER=debezium` and a 32-character password
- [ ] CIS logs show the connector resumed from a stored offset, no access denied
- [ ] a change written to OpenMRS appears on its Kafka topic within 15 seconds
- [ ] `root`@`%` no longer exists, or is documented as still required and why
