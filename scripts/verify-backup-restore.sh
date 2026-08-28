#!/usr/bin/env bash
# ST-10.2.4 — prove the most recent backup actually restores.
#
# An untested backup is not a backup. This restores into throwaway volumes,
# asserts a known marker survived, and emits a Prometheus metric either way. It
# never touches the live volumes: every volume it creates is prefixed
# `bkverify-` and removed at the end.
#
# Usage:  ./scripts/verify-backup-restore.sh [backup-dir]
#         (default: newest directory under NIDAN_BACKUP_DIR / ~/nidan-backups)
#
# Environment:
#   BACKUP_AGE_IDENTITY   path to the private key (required)
#   METRICS_DIR           Prometheus textfile collector dir
#                         (default /var/lib/node_exporter/textfile_collector)
set -uo pipefail

CD="$(cd "$(dirname "$0")/.." && pwd)"
AGE_IMAGE="nidan-backup-age:1.2.1"
PREFIX="bkverify"
METRICS_DIR="${METRICS_DIR:-/var/lib/node_exporter/textfile_collector}"
METRIC_FILE="$METRICS_DIR/backup_restore_verified.prom"

# The assertion runs against the restored *database*, not against a file. A
# tarball that extracts is not the same as a database that starts: a torn
# InnoDB page extracts perfectly and then refuses to open. So the check boots a
# throwaway MariaDB on the restored volume and queries it.
MARKER_VOLUME="openmrs-db-data"
DB_IMAGE="mariadb:10.11"
# Data that must exist for the system to function at all. Deliberately not a
# special row someone has to remember to insert — a marker that can be
# forgotten is a marker that silently stops proving anything.
MARKER_QUERY="SELECT (SELECT COUNT(*) FROM openmrs.location) + (SELECT COUNT(*) FROM openmrs.users)"

result="fail"
reason="did not run"
started=$(date +%s)

emit() {
  local dur=$(( $(date +%s) - started ))
  mkdir -p "$METRICS_DIR" 2>/dev/null || true
  # Written to .tmp then moved: the collector must never read a half-written file.
  {
    echo "# HELP backup_restore_verified Whether the latest backup restored and passed its marker assertion."
    echo "# TYPE backup_restore_verified gauge"
    echo "backup_restore_verified{result=\"$result\"} 1"
    echo "# HELP backup_restore_verified_timestamp_seconds When the check last completed."
    echo "# TYPE backup_restore_verified_timestamp_seconds gauge"
    echo "backup_restore_verified_timestamp_seconds $(date +%s)"
    echo "# HELP backup_restore_duration_seconds How long the check took."
    echo "# TYPE backup_restore_duration_seconds gauge"
    echo "backup_restore_duration_seconds $dur"
  } > "$METRIC_FILE.tmp" 2>/dev/null && mv "$METRIC_FILE.tmp" "$METRIC_FILE" 2>/dev/null \
    || echo "warning: could not write $METRIC_FILE" >&2

  echo "backup_restore_verified{result=\"$result\"} 1   # $reason (${dur}s)"
  [ "$result" = "pass" ] && exit 0 || exit 1
}

cleanup() {
  for v in $(docker volume ls -q --filter "name=^${PREFIX}_" 2>/dev/null); do
    docker volume rm -f "$v" >/dev/null 2>&1
  done
}
trap cleanup EXIT

ROOT="${NIDAN_BACKUP_DIR:-$HOME/nidan-backups}"
DIR="${1:-$(ls -1d "$ROOT"/*/ 2>/dev/null | sort | tail -1)}"

if [ -z "${DIR:-}" ] || [ ! -d "$DIR" ]; then
  reason="no backup directory found under $ROOT"; emit
fi
DIR="$(cd "$DIR" && pwd)"
echo "verifying: $DIR"

if [ -z "${BACKUP_AGE_IDENTITY:-}" ] || [ ! -f "${BACKUP_AGE_IDENTITY:-}" ]; then
  reason="BACKUP_AGE_IDENTITY unset or missing"; emit
fi
IDFILE="$(cd "$(dirname "$BACKUP_AGE_IDENTITY")" && pwd)/$(basename "$BACKUP_AGE_IDENTITY")"

ARCHIVE="$DIR/$MARKER_VOLUME.tar.gz.age"
[ -f "$ARCHIVE" ] || { reason="missing $MARKER_VOLUME.tar.gz.age"; emit; }

# Checksums first: a corrupted archive should be reported as such, not as a
# decryption failure.
if ! ( cd "$DIR" && shasum -a 256 -c SHA256SUMS >/dev/null 2>&1 ); then
  reason="SHA256SUMS mismatch — archive corrupted in storage"; emit
fi

cleanup
docker volume create "${PREFIX}_${MARKER_VOLUME}" >/dev/null 2>&1 \
  || { reason="could not create throwaway volume"; emit; }

if ! docker run --rm \
      -v "${PREFIX}_${MARKER_VOLUME}:/v" -v "$DIR:/in:ro" -v "$IDFILE:/key:ro" "$AGE_IMAGE" \
      sh -c "set -o pipefail; age -d -i /key '/in/$MARKER_VOLUME.tar.gz.age' | tar xzf - -C /v" 2>/dev/null; then
  reason="restore failed: archive did not decrypt or did not extract"; emit
fi

# Boot a throwaway database on the restored volume and query it. This is the
# step that separates "the tar extracted" from "the backup is usable".
CTR="${PREFIX}-db-$$"
docker rm -f "$CTR" >/dev/null 2>&1
# --skip-grant-tables, deliberately. A restored volume carries whatever
# credentials were in the backup, not any we could pass in, so authenticating
# would mean handing this job the production root password. Skipping grants
# instead means the verification job needs no database credential at all. Safe
# here and nowhere else: the container is throwaway, has no published port, and
# --network none leaves it unreachable.
if ! docker run -d --name "$CTR" --network none \
      -e MARIADB_ALLOW_EMPTY_ROOT_PASSWORD=1 \
      -v "${PREFIX}_${MARKER_VOLUME}:/var/lib/mysql" "$DB_IMAGE" \
      --skip-grant-tables --skip-networking=0 >/dev/null 2>&1; then
  reason="restored, but the database container would not start"; emit
fi
trap 'docker rm -f "$CTR" >/dev/null 2>&1; cleanup' EXIT

ready=0
for _ in $(seq 1 60); do
  if docker exec "$CTR" mariadb -uroot -e "SELECT 1" >/dev/null 2>&1; then
    ready=1; break
  fi
  sleep 2
done
[ "$ready" -eq 1 ] || { reason="restored, but the database never became queryable in 120s"; emit; }

found=$(docker exec "$CTR" mariadb -uroot -N -B \
          -e "$MARKER_QUERY" 2>/dev/null | tr -d '[:space:]')

case "$found" in
  ''|*[!0-9]*) reason="restored and started, but the marker query returned nothing"; emit ;;
esac
if [ "$found" -le 0 ]; then
  reason="restored and started, but the database is empty — treat as no backup"; emit
fi

result="pass"
reason="restored, started, marker query returned $found rows"
emit
