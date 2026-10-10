#!/usr/bin/env bash
# Keycloak restore drill (backlog KC-28-06, KC-28-07): back up a Keycloak, restore it somewhere else, log in,
# all within 15 min; and a person disabled AFTER the backup stays locked out once the shipped events are re-applied.
#
#   scripts/keycloak-restore-drill.sh WORKDIR      (outside the repository: a throwaway age key and the backup go there)
#
# Project A: Keycloak on Postgres, realm imported, users "drillnurse" and "drillgone" created; its keycloak-db-data
# volume is backed up with scripts/volume-backup.sh (encrypted to a throwaway key). Then drillgone is disabled on A
# and A's events are shipped (keycloak-event-shipper.py). Project B: the backup is restored into a NEW, empty volume
# and a second Keycloak started on it: drillnurse logs in, and so does drillgone (the restore brought her back);
# keycloak-reapply-after-restore.py then re-applies the revocations and drillgone is refused. Nothing that exists is
# overwritten: project names carry DRILL_ID (default: a timestamp). Both projects are left stopped; remove them by
# hand when done.
set -euo pipefail
W="${1:?usage: $0 WORKDIR (outside the repository)}"
CD="$(cd "$(dirname "$0")/.." && pwd)"
F="$CD/tests/e2e-auth/harness/restore-drill.yml"
LOGIN="$CD/tests/e2e-auth/harness/drill-login.py"
mkdir -p "$W"; W="$(cd "$W" && pwd)"
case "$W/" in "$CD"/*) echo "WORKDIR must be outside the repository" >&2; exit 2 ;; esac
ID="${DRILL_ID:-$(date +%Y%m%d%H%M%S)}"
A="nidan-drill-$ID-a"; B="nidan-drill-$ID-b"
for p in "$A" "$B"; do
  docker volume inspect "${p}_keycloak-db-data" >/dev/null 2>&1 && { echo "volume ${p}_keycloak-db-data exists: remove it first (by hand)" >&2; exit 2; }
done
[ -e "$W/key" ] && { echo "$W/key exists: use a fresh WORKDIR" >&2; exit 2; }
start=$(date +%s)
admin_token() {   # the drill Keycloak's bootstrap admin (throwaway); printed once into a 0600 file by the caller
  curl -s -d grant_type=password -d client_id=admin-cli -d username=admin -d password=harness-admin-password \
    "$1/realms/master/protocol/openid-connect/token" | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])"
}
umask 077
export KC_USERNAME_PATTERN="$(python3 -I "$CD/scripts/reserved-usernames.py" | sed -n 's/^KC_USERNAME_PATTERN=//p')"

echo "== A: Keycloak + realm + drillnurse"
docker compose -p "$A" -f "$F" up -d --wait keycloak
docker compose -p "$A" -f "$F" --profile import run --rm config-cli >/dev/null
python3 -I "$LOGIN" create http://127.0.0.1:18190/auth drillnurse
python3 -I "$LOGIN" create http://127.0.0.1:18190/auth drillgone
python3 -I "$LOGIN" login http://127.0.0.1:18190/auth drillnurse

echo "== backup A (encrypted)"
"$CD/scripts/volume-backup.sh" keygen "$W/key" >/dev/null
export BACKUP_AGE_RECIPIENT="$(grep -o 'age1[a-z0-9]*' "$W/key" | tail -1)"
backup_ms=$(( $(date +%s) * 1000 ))
COMPOSE_PROJECT_NAME="$A" "$CD/scripts/volume-backup.sh" backup "$W/backup" keycloak-db-data

echo "== after the backup: drillgone is disabled on A, events shipped off the database"
docker compose -p "$A" -f "$F" up -d --wait keycloak
python3 -I "$LOGIN" disable http://127.0.0.1:18190/auth drillgone
admin_token http://127.0.0.1:18190/auth > "$W/token-a"
KC_TOKEN_FILE="$W/token-a" python3 -I "$CD/scripts/keycloak-event-shipper.py" "$W/events" --url http://127.0.0.1:18190/auth
docker compose -p "$A" -f "$F" stop >/dev/null

echo "== restore into B (new volume) and log in there"
BACKUP_AGE_IDENTITY="$W/key" COMPOSE_PROJECT_NAME="$B" "$CD/scripts/volume-backup.sh" restore "$W/backup" keycloak-db-data
DRILL_PORT=18191 docker compose -p "$B" -f "$F" up -d --wait keycloak
python3 -I "$LOGIN" login http://127.0.0.1:18191/auth drillnurse
python3 -I "$LOGIN" login http://127.0.0.1:18191/auth drillgone >/dev/null \
  && echo "   drillgone can log in on the restored Keycloak (expected before the re-apply)" \
  || { echo "drillgone could not log in before the re-apply: the drill proves nothing" >&2; exit 1; }
admin_token http://127.0.0.1:18191/auth > "$W/token-b"
KC_TOKEN_FILE="$W/token-b" python3 -I "$CD/scripts/keycloak-reapply-after-restore.py" "$W/events" --since "$backup_ms" --apply \
  --url http://127.0.0.1:18191/auth
if python3 -I "$LOGIN" login http://127.0.0.1:18191/auth drillgone; then
  echo "drillgone still logs in after the re-apply (KC-28-07)" >&2; exit 1
fi
python3 -I "$LOGIN" login http://127.0.0.1:18191/auth drillnurse
docker compose -p "$B" -f "$F" stop >/dev/null

elapsed=$(( $(date +%s) - start ))
echo "restore drill: OK in ${elapsed} s (limit 900 s); drillgone stayed out after the re-apply"
[ "$elapsed" -le 900 ]
