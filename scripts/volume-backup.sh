#!/usr/bin/env bash
# Cold backup/restore of the nidan stateful volumes, encrypted at rest.
#
# THE OUTPUT DIRECTORY MUST BE OUTSIDE THE REPOSITORY. Inside the working tree
# these archives are one `git add -A` away from publication, and no ignore rule
# protects against a copy, an editor indexer, or a backup tool that walks the
# repo. The convention is ~/nidan-backups, which is the default below. A
# destination inside the repository is refused, not warned about.
#
# ARCHIVES ARE ENCRYPTED (S-10.2). tar output is piped straight into age, so a
# plaintext tarball never exists on disk at any point, including on failure
# paths. There is no unencrypted mode and no fallback: a missing recipient is a
# hard error, because a backup that silently degrades to plaintext is worse than
# one that fails loudly.
#
# Usage:  ./scripts/volume-backup.sh backup [outdir]   (default ~/nidan-backups/<ts>)
#         ./scripts/volume-backup.sh backup <dir> <vol>...   (refresh a subset)
#         ./scripts/volume-backup.sh restore <dir> [vol]...
#         ./scripts/volume-backup.sh keygen <file>           (create a keypair)
#         ./scripts/volume-backup.sh prune <root> [--execute] (retention: 14 daily + 12 monthly, KC-28-08)
#
# Environment:
#         BACKUP_AGE_RECIPIENT   age public key (age1...) — required to back up
#         BACKUP_AGE_IDENTITY    path to the private key file — required to restore
#         NIDAN_BACKUP_DIR       override the default output root
#         METRICS_DIR            Prometheus textfile collector (default /var/lib/node_exporter/textfile_collector)
#
# WARNING on restore: it replaces each volume wholesale, it does not merge.
# Anything written since the backup is lost. Decryption is verified before
# anything is deleted — see verify_decryptable below.
set -euo pipefail

P="${COMPOSE_PROJECT_NAME:-nidan-docker}"
# Every named volume in docker-compose.yml is in VOLS or EXCLUDED (scripts/nidan-auth-check.py fails otherwise,
# backlog KC-28-02). Keycloak's DB and the gateway access log are audit records (spec A4) and are backed up.
VOLS=(odoo-data odoo-db-data openmrs-data openmrs-db-data nidan-integration-db-data
      openelis-db-data openelis-properties openelis-lucene-index orthanc-data orthanc-db-data
      dhis2-integration-data dhis2-integration-db-data dhis2-integration-app-logs dhis2-integration-audit-logs
      superset-home keycloak-db-data gateway-logs certbot-etc)
EXCLUDED=(certbot-www openelis-logs openelis-tomcat-logs)   # ACME challenge files (transient); OpenELIS/Tomcat logs: not records, may hold patient details, kept on the host only (KC-45-03)
CD="$(cd "$(dirname "$0")/.." && pwd)"
AGE_IMAGE="nidan-backup-age:1.2.1"

# containers must be stopped: a live DB's files are not a consistent snapshot
stack() { (cd "$CD" && docker compose "$@"); }
# The project's running containers, whatever compose profile started them; stopped and restarted as a set.
running_containers() { docker ps -q --filter "label=com.docker.compose.project=$P"; }
stop_running() { RUNNING="$(running_containers)"; [ -z "$RUNNING" ] || docker stop $RUNNING >/dev/null; }
start_stopped() { [ -z "${RUNNING:-}" ] || docker start $RUNNING >/dev/null; }

# Build the pinned age image on first use. Cheap after that; keeps the tool
# version identical on every machine that might have to run a restore.
ensure_age_image() {
  docker image inspect "$AGE_IMAGE" >/dev/null 2>&1 && return 0
  echo "building $AGE_IMAGE (first run only)" >&2
  docker build -q -t "$AGE_IMAGE" "$CD/backup-age" >/dev/null
}

require_recipient() {
  if [ -z "${BACKUP_AGE_RECIPIENT:-}" ]; then
    echo "refusing to write an unencrypted backup: BACKUP_AGE_RECIPIENT is unset" >&2
    echo "generate a keypair with: $0 keygen ~/nidan-backup-key.txt" >&2
    exit 1
  fi
  case "$BACKUP_AGE_RECIPIENT" in
    age1*) ;;
    *) echo "refusing to write an unencrypted backup: BACKUP_AGE_RECIPIENT is not an age public key" >&2
       exit 1 ;;
  esac
}

require_identity() {
  if [ -z "${BACKUP_AGE_IDENTITY:-}" ]; then
    echo "cannot restore: BACKUP_AGE_IDENTITY is unset" >&2
    exit 1
  fi
  if [ ! -f "$BACKUP_AGE_IDENTITY" ]; then
    echo "cannot restore: identity file not found: $BACKUP_AGE_IDENTITY" >&2
    exit 1
  fi
}

# Alert (KC-28-03): same Prometheus textfile collector as verify-backup-restore.sh. backup_succeeded 0 or a stale
# backup_last_run_timestamp_seconds (no run in > 26 h) must page someone.
backup_metric() {
  local dir="${METRICS_DIR:-/var/lib/node_exporter/textfile_collector}" f
  f="$dir/nidan_backup.prom"
  mkdir -p "$dir" 2>/dev/null || true
  { echo "# HELP backup_succeeded Whether the last nightly volume backup completed (1) or failed (0)."
    echo "# TYPE backup_succeeded gauge"
    echo "backup_succeeded $1"
    echo "# HELP backup_last_run_timestamp_seconds When the last backup run ended."
    echo "# TYPE backup_last_run_timestamp_seconds gauge"
    echo "backup_last_run_timestamp_seconds $(date +%s)"
    echo "# HELP backup_duration_seconds How long the last backup run took."
    echo "# TYPE backup_duration_seconds gauge"
    echo "backup_duration_seconds $(( $(date +%s) - ${STARTED:-$(date +%s)} ))"
  } > "$f.tmp" 2>/dev/null && mv "$f.tmp" "$f" 2>/dev/null || echo "warning: could not write $f" >&2
}

# Prove an archive decrypts AND is a well-formed tar, writing nothing. This runs
# before any volume is touched. The previous version wiped the volume and then
# extracted, so a bad key or a truncated archive destroyed the data it was
# supposed to restore.
verify_decryptable() {
  local v="$1" dir="$2" idfile="$3"
  docker run --rm \
    -v "$dir:/in:ro" -v "$idfile:/key:ro" "$AGE_IMAGE" \
    sh -c "age -d -i /key '/in/$v.tar.gz.age' | tar -tzf - > /dev/null"
}

case "${1:-}" in
keygen)
  OUT="${2:?usage: $0 keygen <file>}"
  [ -e "$OUT" ] && { echo "refusing to overwrite an existing key: $OUT" >&2; exit 1; }
  ensure_age_image
  ( umask 077; docker run --rm "$AGE_IMAGE" age-keygen > "$OUT" 2>/dev/null )
  chmod 600 "$OUT"
  PUB=$(grep -o 'age1[a-z0-9]*' "$OUT" | tail -1)
  cat <<MSG
keypair written to $OUT (mode 0600)

  BACKUP_AGE_RECIPIENT=$PUB
  BACKUP_AGE_IDENTITY=$OUT

Put the recipient in .env. Keep the identity file OFF this machine as well —
if it is lost, every backup encrypted to it is unrecoverable. See
docs/RUNBOOK-backup-key-management.md.
MSG
  ;;

backup)
  require_recipient
  # Only the public key belongs on the stack host; the private key lives where restores are verified (KC-28-05).
  if [ -n "${BACKUP_AGE_IDENTITY:-}" ]; then
    echo "refusing to back up with BACKUP_AGE_IDENTITY set: the private key must not be on the stack host" >&2
    exit 1
  fi
  ensure_age_image
  DIR="${2:-${NIDAN_BACKUP_DIR:-$HOME/nidan-backups}/$(date +%Y%m%d-%H%M%S)}"
  mkdir -p "$DIR"; DIR="$(cd "$DIR" && pwd)"   # docker -v needs an absolute host path
  # Refuse a destination inside the repository, however we got here: the
  # default, an explicit argument, or a symlink that resolves back in.
  case "$DIR/" in
    "$CD"/*)
      rmdir "$DIR" 2>/dev/null || true
      echo "refusing to write patient data inside the repository: $DIR" >&2
      echo "pass a path outside $CD, or unset the argument to use ${NIDAN_BACKUP_DIR:-$HOME/nidan-backups}" >&2
      exit 1 ;;
  esac
  [ $# -gt 2 ] && VOLS=("${@:3}")              # refresh only the named volumes in an existing dir
  STARTED=$(date +%s)
  # Whatever happens from here (a failed archive, a full disk, an interrupt), the stack is started again and a
  # failure is reported: as the backup_succeeded metric and on stderr (cron mail). An incomplete folder has no
  # SHA256SUMS, so neither restore nor prune ever counts it as a backup (KC-28-03).
  # set +e first: under set -e one failing command would end the trap before the stack is back or the alert is out.
  # An interrupt (cron killed, Ctrl-C) exits non-zero, so it is reported as a failure, not a success.
  trap 'rc=$?; set +e
        if ! start_stopped; then echo "BACKUP: could not start the stack again: docker start $RUNNING" >&2; rc=1; fi
        if [ "$rc" -ne 0 ]; then backup_metric 0; echo "BACKUP FAILED (exit $rc): $DIR is incomplete or the stack is down" >&2; fi
        exit "$rc"' EXIT
  trap 'exit 143' TERM
  trap 'exit 130' INT
  stop_running
  for v in "${VOLS[@]}"; do
    # a volume this site does not use (e.g. keycloak-db-data without central login): skip, never create it
    if ! docker volume inspect "${P}_${v}" >/dev/null 2>&1; then echo "-- $v: not on this host, skipped"; continue; fi
    echo "-> $v"
    # tar streams into age inside one container: the plaintext never becomes a
    # file. Written to .part first so an interrupted run cannot leave a
    # truncated archive that looks complete to a later restore.
    docker run --rm -e R="$BACKUP_AGE_RECIPIENT" \
      -v "${P}_${v}:/v:ro" -v "$DIR:/out" "$AGE_IMAGE" \
      sh -c "set -o pipefail; tar czf - -C /v . | age -r \"\$R\" -o '/out/$v.tar.gz.age.part'"
    # an empty or garbled archive is a failure, never a backup (every age file starts with its header line)
    if [ "$(head -c 21 "$DIR/$v.tar.gz.age.part" 2>/dev/null)" != "age-encryption.org/v1" ]; then
      echo "$v: archive is empty or not age-encrypted" >&2
      exit 1
    fi
    mv "$DIR/$v.tar.gz.age.part" "$DIR/$v.tar.gz.age"
  done
  # Checksums before the restart, deliberately. They describe the archives and do
  # not need the stack up, and under `set -e` a failed `stack start` would
  # otherwise abort the script here and leave a complete-looking backup with no
  # manifest — which a later restore would refuse.
  ( cd "$DIR" && shasum -a 256 ./*.tar.gz.age > SHA256SUMS )
  backup_metric 1
  du -sh "$DIR"/*; echo "backup: $DIR (encrypted to ${BACKUP_AGE_RECIPIENT})"
  # the EXIT trap starts the stack again
  ;;

restore)
  DIR="${2:?usage: $0 restore <dir>}"; DIR="$(cd "$DIR" && pwd)"   # docker -v needs an absolute host path
  require_identity
  ensure_age_image
  IDFILE="$(cd "$(dirname "$BACKUP_AGE_IDENTITY")" && pwd)/$(basename "$BACKUP_AGE_IDENTITY")"
  if [ $# -gt 2 ]; then
    VOLS=("${@:3}")                            # restore only the named volumes
  else
    # default: every volume archived in this backup (older backups may hold fewer volumes)
    VOLS=(); for f in "$DIR"/*.tar.gz.age; do [ -e "$f" ] && VOLS+=("$(basename "$f" .tar.gz.age)"); done
    [ ${#VOLS[@]} -gt 0 ] || { echo "no archives in $DIR" >&2; exit 1; }
  fi
  for v in "${VOLS[@]}"; do
    [ -f "$DIR/$v.tar.gz.age" ] || { echo "missing $DIR/$v.tar.gz.age" >&2; exit 1; }
  done
  ( cd "$DIR" && shasum -a 256 -c SHA256SUMS )

  # Every archive must decrypt and parse before a single byte is deleted.
  # One bad key must not cost the volumes that would have restored fine.
  echo "verifying all archives decrypt before touching any volume"
  for v in "${VOLS[@]}"; do
    printf '   %s ... ' "$v"
    if verify_decryptable "$v" "$DIR" "$IDFILE"; then
      echo "ok"
    else
      echo "FAILED"
      echo "cannot restore: $v.tar.gz.age did not decrypt with $BACKUP_AGE_IDENTITY" >&2
      echo "no volume has been modified." >&2
      exit 1
    fi
  done

  stop_running
  for v in "${VOLS[@]}"; do
    echo "-> $v"
    docker volume create "${P}_${v}" >/dev/null
    docker run --rm -e V="$v" \
      -v "${P}_${v}:/v" -v "$DIR:/in:ro" -v "$IDFILE:/key:ro" "$AGE_IMAGE" \
      sh -c 'set -o pipefail; rm -rf /v/..?* /v/.[!.]* /v/* 2>/dev/null; age -d -i /key "/in/$V.tar.gz.age" | tar xzf - -C /v'
  done
  start_stopped
  echo "restored from $DIR"
  ;;

prune)
  # Retention (KC-28-08): keep the 14 newest complete backups, plus the newest complete backup of each of the
  # last 12 months that have one. Only <root>/YYYYMMDD-HHMMSS folders with a SHA256SUMS (complete) are
  # candidates; anything else in <root> is never touched. Lists by default; deletes only with --execute.
  ROOT="${2:?usage: $0 prune <root> [--execute]}"
  KEEP=(); COUNT=0; MONTHS=""
  for d in $(ls -1 "$ROOT" 2>/dev/null | grep -E '^[0-9]{8}-[0-9]{6}$' | sort -r); do
    [ -f "$ROOT/$d/SHA256SUMS" ] || { echo "   keep (incomplete, never pruned): $d"; continue; }
    m="${d:0:6}"
    if [ "$COUNT" -lt 14 ]; then
      KEEP+=("$d"); COUNT=$((COUNT + 1))
      case " $MONTHS " in *" $m "*) ;; *) MONTHS="$MONTHS $m" ;; esac
    elif ! case " $MONTHS " in *" $m "*) true ;; *) false ;; esac && [ "$(echo $MONTHS | wc -w)" -lt 12 ]; then
      KEEP+=("$d"); MONTHS="$MONTHS $m"
    else
      if [ "${3:-}" = "--execute" ]; then rm -rf "${ROOT:?}/$d"; echo "   deleted: $d"; else echo "   would delete: $d"; fi
      continue
    fi
    echo "   keep: $d"
  done
  [ "${3:-}" = "--execute" ] || echo "dry run: nothing deleted (add --execute)"
  ;;

*) grep -E '^# (Usage|         )' "$0" | sed 's/^# //'; exit 1 ;;
esac
