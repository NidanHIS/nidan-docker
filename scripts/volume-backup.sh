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
#
# Environment:
#         BACKUP_AGE_RECIPIENT   age public key (age1...) — required to back up
#         BACKUP_AGE_IDENTITY    path to the private key file — required to restore
#         NIDAN_BACKUP_DIR       override the default output root
#
# WARNING on restore: it replaces each volume wholesale, it does not merge.
# Anything written since the backup is lost. Decryption is verified before
# anything is deleted — see verify_decryptable below.
set -euo pipefail

P="${COMPOSE_PROJECT_NAME:-nidan-docker}"
VOLS=(odoo-data odoo-db-data openmrs-data openmrs-db-data nidan-integration-db-data)
SVCS=(odoo odoo-db openmrs-backend openmrs-db nidan-integration-db nidan-cis nidan-ois)
CD="$(cd "$(dirname "$0")/.." && pwd)"
AGE_IMAGE="nidan-backup-age:1.2.1"

# containers must be stopped: a live DB's files are not a consistent snapshot
stack() { (cd "$CD" && docker compose "$@"); }

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
  stack stop "${SVCS[@]}"
  for v in "${VOLS[@]}"; do
    echo "-> $v"
    # tar streams into age inside one container: the plaintext never becomes a
    # file. Written to .part first so an interrupted run cannot leave a
    # truncated archive that looks complete to a later restore.
    docker run --rm -e R="$BACKUP_AGE_RECIPIENT" \
      -v "${P}_${v}:/v:ro" -v "$DIR:/out" "$AGE_IMAGE" \
      sh -c "set -o pipefail; tar czf - -C /v . | age -r \"\$R\" -o '/out/$v.tar.gz.age.part'"
    mv "$DIR/$v.tar.gz.age.part" "$DIR/$v.tar.gz.age"
  done
  # Checksums before the restart, deliberately. They describe the archives and do
  # not need the stack up, and under `set -e` a failed `stack start` would
  # otherwise abort the script here and leave a complete-looking backup with no
  # manifest — which a later restore would refuse.
  ( cd "$DIR" && shasum -a 256 ./*.tar.gz.age > SHA256SUMS )
  stack start "${SVCS[@]}"
  du -sh "$DIR"/*; echo "backup: $DIR (encrypted to ${BACKUP_AGE_RECIPIENT})"
  ;;

restore)
  DIR="${2:?usage: $0 restore <dir>}"; DIR="$(cd "$DIR" && pwd)"   # docker -v needs an absolute host path
  require_identity
  ensure_age_image
  IDFILE="$(cd "$(dirname "$BACKUP_AGE_IDENTITY")" && pwd)/$(basename "$BACKUP_AGE_IDENTITY")"
  [ $# -gt 2 ] && VOLS=("${@:3}")              # restore only the named volumes
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

  stack stop "${SVCS[@]}"
  for v in "${VOLS[@]}"; do
    echo "-> $v"
    docker volume create "${P}_${v}" >/dev/null
    docker run --rm -e V="$v" \
      -v "${P}_${v}:/v" -v "$DIR:/in:ro" -v "$IDFILE:/key:ro" "$AGE_IMAGE" \
      sh -c 'set -o pipefail; rm -rf /v/..?* /v/.[!.]* /v/* 2>/dev/null; age -d -i /key "/in/$V.tar.gz.age" | tar xzf - -C /v'
  done
  stack start "${SVCS[@]}"
  echo "restored from $DIR"
  ;;

*) grep -E '^# (Usage|         )' "$0" | sed 's/^# //'; exit 1 ;;
esac
