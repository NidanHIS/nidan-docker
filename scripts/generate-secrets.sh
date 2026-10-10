#!/bin/sh
# Generate unique per-site secrets into .env.local (untracked, mode 0600). Backlog KC-05.
#
#   scripts/generate-secrets.sh --fresh-install [TARGET]      (TARGET defaults to .env.local)
#   then:  COMPOSE_ENV_FILES=.env,.env.local docker compose up -d
#
# Fills every env.template key whose value is CHANGEME_GENERATE_ME, except external credentials
# (HMIS/DHIS2 servers, backup public key) and keystore passwords, which must come from their owners.
# Never overwrites a real value already in TARGET (a CHANGEME placeholder counts as missing, so
# `cp env.template .env.local` first is fine); re-running only fills gaps.
#
# --fresh-install is required: database passwords only take effect when the DB volumes are created.
# On a site that already has data, changing them locks the apps out of their databases; rotate those
# with the rotation runbook instead.
set -eu

cd "$(dirname "$0")/.."
[ "${1:-}" = "--fresh-install" ] || { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
TARGET="${2:-.env.local}"

if git ls-files --error-unmatch "$TARGET" >/dev/null 2>&1; then
  echo "refusing: $TARGET is tracked by git; secrets must go to an untracked file" >&2; exit 1
fi
if ! git check-ignore -q "$TARGET"; then
  echo "refusing: $TARGET is not git-ignored; add it to .gitignore first" >&2; exit 1
fi

# Supplied by their owners, never generated.
SKIP="FEDERAL_HMIS_PASSWORD PROVINCIAL_DHIS2_PASSWORD BACKUP_AGE_RECIPIENT SSL_TRUSTSTORE_PASSWORD SSL_KEYSTORE_PASSWORD"
# follower=leader: the two sides of one credential must hold the same value.
PAIRS="OIS_WEBHOOK_SECRET=NIDAN_OIS_SECRET ODOO_NIDAN_SECRET=NIDAN_ODOO_SECRET OPENMRS_PASSWORD=NIDAN_SYNC_PASSWORD OPENELIS_PASSWORD=OPENELIS_DEFAULT_ADMIN_PASSWORD ODOO_PASSWORD=ODOO_ADMIN_PASSWORD"

umask 077
touch "$TARGET"
chmod 600 "$TARGET"

value_of() { grep "^$1=" "$TARGET" | head -1 | cut -d= -f2-; }
# A key counts as set only with a real value; empty or CHANGEME* placeholders are filled.
has() { v="$(value_of "$1")"; [ -n "$v" ] && case "$v" in CHANGEME*) false ;; *) true ;; esac; }
set_key() {  # replace KEY's line if present (placeholder), else append
  if grep -q "^$1=" "$TARGET"; then
    awk -v k="$1" -v v="$2" 'index($0, k "=") == 1 && !done { print k "=" v; done = 1; next } { print }' "$TARGET" > "$TARGET.tmp"
    cat "$TARGET.tmp" > "$TARGET"; rm -f "$TARGET.tmp"
  else
    echo "$1=$2" >> "$TARGET"
  fi
}
# 40 chars of [A-Za-z0-9]: safe inside JDBC/Postgres URLs, JSON, shell and Java -D flags.
# Always upper, lower and digit: OpenMRS's password rules refuse a value without one (about 1 in 1100 draws had no
# digit), and the OpenMRS admin password is set from OPENMRS_ADMIN_PASSWORD (nidancore, KC-05-06).
random() {
  while :; do
    v=$(openssl rand -base64 96 | tr -dc 'A-Za-z0-9' | cut -c1-40)
    case "$v" in *[0-9]*) ;; *) continue ;; esac
    case "$v" in *[a-z]*) ;; *) continue ;; esac
    case "$v" in *[A-Z]*) echo "$v"; return ;; esac
  done
}
leader_of() { for p in $PAIRS; do [ "${p%%=*}" = "$1" ] && { echo "${p#*=}"; return; }; done; }

added=0
for key in $(grep -E '^[A-Z0-9_]+=CHANGEME_GENERATE_ME' env.template | cut -d= -f1); do
  case " $SKIP " in *" $key "*) continue ;; esac
  [ -z "$(leader_of "$key")" ] || continue   # followers are written after their leader
  case "$key" in
    OAUTH2_PROXY_COOKIE_SECRET) value=$(random | cut -c1-32) ;;  # oauth2-proxy: exactly 16/24/32 bytes
    *) value=$(random) ;;
  esac
  has "$key" || { set_key "$key" "$value"; added=$((added + 1)); }
done
for p in $PAIRS; do
  follower="${p%%=*}"; leader="${p#*=}"
  has "$leader" || continue
  if has "$follower"; then
    [ "$(value_of "$follower")" = "$(value_of "$leader")" ] || echo "warning: $follower differs from $leader in $TARGET (they must match)" >&2
  else
    set_key "$follower" "$(value_of "$leader")"; added=$((added + 1))
  fi
done

# The realm's reserved-username rule (KC-55): without it, names like admin or nidan-sync can be created in Keycloak.
# Fixed names + env usernames + client ids; regenerate with --site-file <workbook>/reserved-usernames.txt at cutover.
if ! has KC_USERNAME_PATTERN; then
  pattern="$(python3 -I "$(dirname "$0")/reserved-usernames.py" 2>/dev/null | sed -n 's/^KC_USERNAME_PATTERN=//p')"
  if [ -n "$pattern" ]; then set_key KC_USERNAME_PATTERN "$pattern"; added=$((added + 1))
  else echo "warning: could not compute KC_USERNAME_PATTERN (scripts/reserved-usernames.py)" >&2; fi
fi

echo "$TARGET: $added keys set ($(grep -c '=' "$TARGET") lines with values). Still to fill by hand:$(for k in $SKIP; do has "$k" || printf ' %s' "$k"; done)"
