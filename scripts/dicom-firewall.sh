#!/bin/sh
# Only the modality LAN may reach Orthanc's DICOM port (backlog KC-31-03).
#   DICOM_MODALITY_CIDR=192.168.10.0/24 scripts/dicom-firewall.sh [--apply]
# Rules go into Docker's DOCKER-USER chain (Docker's own rules would bypass INPUT), match the ORIGINAL
# destination port 4242 via conntrack (so the container port mapping cannot dodge them), and carry the
# comment "nidan-dicom": a re-run first removes only those, then inserts accept-from-CIDR above drop-all.
# Dry run by default (prints the iptables commands). Not persistent across reboots: add it to the host's
# firewall service (e.g. iptables-persistent) or run it from a boot unit.
set -eu
CIDR="${DICOM_MODALITY_CIDR:-}"
PORT="${DICOM_PORT:-4242}"
IPT="${IPTABLES:-iptables}"
APPLY=false; [ "${1:-}" = "--apply" ] && APPLY=true
echo "$CIDR" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}/([0-9]|[12][0-9]|3[0-2])$' \
  || { echo "set DICOM_MODALITY_CIDR to the modality network, e.g. 192.168.10.0/24" >&2; exit 2; }
run() { if $APPLY; then "$@"; else echo "  $*"; fi; }
MATCH="-p tcp -m conntrack --ctorigdstport $PORT --ctdir ORIGINAL -m comment --comment nidan-dicom"
# remove previous nidan-dicom rules (only ours, matched by the comment)
if $APPLY; then
  while $IPT -S DOCKER-USER 2>/dev/null | grep -q -- '--comment nidan-dicom'; do
    rule="$($IPT -S DOCKER-USER | grep -- '--comment nidan-dicom' | head -n 1 | sed 's/^-A DOCKER-USER //')"
    # shellcheck disable=SC2086
    $IPT -D DOCKER-USER $rule
  done
else
  echo "  (remove existing DOCKER-USER rules tagged nidan-dicom)"
fi
# insert drop first, then accept above it (both at position 1 -> accept ends up first)
# shellcheck disable=SC2086
run $IPT -I DOCKER-USER 1 $MATCH -j DROP
# shellcheck disable=SC2086
run $IPT -I DOCKER-USER 1 -s "$CIDR" $MATCH -j ACCEPT
$APPLY || echo "dry run: nothing changed (add --apply, as root)"
