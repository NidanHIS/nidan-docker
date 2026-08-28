#!/usr/bin/env bash
# Cold backup/restore of the nidan stateful volumes.
#
# THE OUTPUT DIRECTORY MUST BE OUTSIDE THE REPOSITORY. These tarballs are
# plaintext patient data — roughly 500 MB of it, written mode 0644. Inside the
# working tree they are one `git add -A` away from publication, and no ignore
# rule protects against a copy, an editor indexer, or a backup tool that walks
# the repo. The convention is ~/nidan-backups, which is the default below.
# A destination inside the repository is refused, not warned about.
#
# Usage:  ./scripts/volume-backup.sh backup [outdir]   (default ~/nidan-backups/<ts>)
#         ./scripts/volume-backup.sh backup <dir> <vol>...   (refresh a subset)
#         ./scripts/volume-backup.sh restore <dir> [vol]...
#
# Override the default root with NIDAN_BACKUP_DIR.
#
# WARNING on restore: it replaces each volume wholesale (rm -rf then untar),
# it does not merge. Anything written since the backup is lost.
set -euo pipefail

P="${COMPOSE_PROJECT_NAME:-nidan-docker}"
VOLS=(odoo-data odoo-db-data openmrs-data openmrs-db-data nidan-integration-db-data)
SVCS=(odoo odoo-db openmrs-backend openmrs-db nidan-integration-db nidan-cis nidan-ois)
CD="$(cd "$(dirname "$0")/.." && pwd)"

# containers must be stopped: a live DB's files are not a consistent snapshot
stack() { (cd "$CD" && docker compose "$@"); }

case "${1:-}" in
backup)
  DIR="${2:-${NIDAN_BACKUP_DIR:-$HOME/nidan-backups}/$(date +%Y%m%d-%H%M%S)}"
  mkdir -p "$DIR"; DIR="$(cd "$DIR" && pwd)"   # docker -v needs an absolute host path
  # Refuse a destination inside the repository, however we got here: the
  # default, an explicit argument, or a symlink that resolves back in.
  case "$DIR/" in
    "$CD"/*)
      rmdir "$DIR" 2>/dev/null || true
      echo "refusing to write plaintext patient data inside the repository: $DIR" >&2
      echo "pass a path outside $CD, or unset the argument to use ${NIDAN_BACKUP_DIR:-$HOME/nidan-backups}" >&2
      exit 1 ;;
  esac
  [ $# -gt 2 ] && VOLS=("${@:3}")              # refresh only the named volumes in an existing dir
  stack stop "${SVCS[@]}"
  for v in "${VOLS[@]}"; do
    echo "-> $v"
    docker run --rm -v "${P}_${v}:/v:ro" -v "$DIR:/out" alpine \
      tar czf "/out/$v.tar.gz" -C /v .
  done
  stack start "${SVCS[@]}"
  ( cd "$DIR" && shasum -a 256 ./*.tar.gz > SHA256SUMS )
  du -sh "$DIR"/*; echo "backup: $DIR"
  ;;
restore)
  DIR="${2:?usage: $0 restore <dir>}"; DIR="$(cd "$DIR" && pwd)"   # docker -v needs an absolute host path
  [ $# -gt 2 ] && VOLS=("${@:3}")              # restore only the named volumes
  for v in "${VOLS[@]}"; do [ -f "$DIR/$v.tar.gz" ] || { echo "missing $DIR/$v.tar.gz"; exit 1; }; done
  ( cd "$DIR" && shasum -a 256 -c SHA256SUMS )
  stack stop "${SVCS[@]}"
  for v in "${VOLS[@]}"; do
    echo "-> $v"
    docker volume create "${P}_${v}" >/dev/null
    docker run --rm -v "${P}_${v}:/v" -v "$DIR:/in:ro" alpine sh -c \
      'rm -rf /v/..?* /v/.[!.]* /v/* 2>/dev/null; tar xzf "/in/'"$v"'.tar.gz" -C /v'
  done
  stack start "${SVCS[@]}"
  echo "restored from $DIR"
  ;;
*) grep -E '^# (Usage|      )' "$0" | sed 's/^# //'; exit 1 ;;
esac
