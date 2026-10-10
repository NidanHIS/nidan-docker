#!/bin/sh
# Daily rotation of the gateway access log (backlog KC-13-03, spec A2/D53): PHI-free, mode 0600, on its own volume,
# kept 365 days. Run by crond in the gateway container; no logrotate package needed.
#   rotate-access-log.sh [LOG_DIR] [KEEP_DAYS]
set -eu
LOG_DIR="${1:-/var/log/nidan}"
KEEP_DAYS="${2:-365}"
NGINX="${NGINX:-nginx}"
LOG="$LOG_DIR/access.log"
umask 077
[ -s "$LOG" ] || exit 0                       # nothing logged since the last rotation
stamp="$(date +%Y%m%d-%H%M%S)"
mv "$LOG" "$LOG_DIR/access-$stamp.log"
"$NGINX" -s reopen                            # nginx opens a fresh access.log (umask 077 -> 0600)
gzip -9 "$LOG_DIR/access-$stamp.log"
chmod 600 "$LOG_DIR/access-$stamp.log.gz"
# retention: older than KEEP_DAYS days is removed (D53); nothing else in LOG_DIR is touched
find "$LOG_DIR" -maxdepth 1 -type f -name 'access-*.log.gz' -mtime +"$KEEP_DAYS" -exec rm -f {} +
