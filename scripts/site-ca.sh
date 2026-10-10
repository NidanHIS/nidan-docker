#!/bin/sh
# Per-site CA for bare-IP / LAN-hostname hospitals (backlog KC-12-04/05, D55).
#
#   scripts/site-ca.sh OUT_DIR NAME [NAME ...]        NAME = the site's IP(s) and/or LAN hostname(s)
#
# Run on the OPERATOR's machine, not the hospital server. Writes into OUT_DIR (must not exist yet,
# must be outside any git checkout):
#   ca/ca.key            CA private key (0600). Keep offline (encrypted USB / password vault).
#                        Never copy it to the server: whoever holds it can issue certificates.
#   ca/ca.crt            CA certificate. Installed as a trusted root on staff PCs.
#   gateway/privkey.pem  gateway key  } copy both to the server's
#   gateway/fullchain.pem gateway cert} /etc/letsencrypt/live/${GATEWAY_DOMAIN}/
#   install-windows.cmd, install-linux.sh   trust ca.crt on a staff PC (run as admin/root)
#
# The CA is name-constrained (critical nameConstraints = exactly the NAMEs): even a stolen CA key
# cannot mint a certificate for any other site (banking, webmail) that browsers on these PCs accept.
set -eu

[ $# -ge 2 ] || { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
OUT="$1"; shift

if [ -e "$OUT" ] && [ -n "$(ls -A "$OUT" 2>/dev/null)" ]; then
  echo "refusing: $OUT exists and is not empty (nothing is overwritten)" >&2; exit 1
fi
# Check the nearest existing ancestor for a git checkout BEFORE creating anything.
probe="$OUT"; while [ ! -d "$probe" ]; do probe="$(dirname "$probe")"; done
if git -C "$probe" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "refusing: $OUT is inside a git checkout; a CA key must never be committed" >&2; exit 1
fi
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

is_ip() { echo "$1" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; }
SAN=""; NC=""
for n in "$@"; do
  if is_ip "$n"; then SAN="${SAN:+$SAN,}IP:$n"; NC="${NC:+$NC,}permitted;IP:$n/255.255.255.255"
  else SAN="${SAN:+$SAN,}DNS:$n"; NC="${NC:+$NC,}permitted;DNS:$n"; fi
done
FIRST="$1"

umask 077
mkdir -p "$OUT/ca" "$OUT/gateway"
cat > "$OUT/ca/ca.cnf" <<EOF
[req]
distinguished_name = dn
prompt = no
[dn]
CN = Nidan site CA ($FIRST)
O = Nidan EHR site
[v3_ca]
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
nameConstraints = critical, $NC
EOF
openssl req -x509 -new -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -days 3650 \
  -config "$OUT/ca/ca.cnf" -extensions v3_ca -keyout "$OUT/ca/ca.key" -out "$OUT/ca/ca.crt" 2>/dev/null

cat > "$OUT/gateway/leaf.cnf" <<EOF
[v3_leaf]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = $SAN
authorityKeyIdentifier = keyid
subjectKeyIdentifier = hash
EOF
openssl req -new -nodes -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -subj "/CN=$FIRST" \
  -keyout "$OUT/gateway/privkey.pem" -out "$OUT/gateway/req.csr" 2>/dev/null
# 397 days: the longest validity browsers accept for a server certificate.
openssl x509 -req -in "$OUT/gateway/req.csr" -CA "$OUT/ca/ca.crt" -CAkey "$OUT/ca/ca.key" -CAcreateserial \
  -days 397 -extfile "$OUT/gateway/leaf.cnf" -extensions v3_leaf -out "$OUT/gateway/cert.pem" 2>/dev/null
cat "$OUT/gateway/cert.pem" "$OUT/ca/ca.crt" > "$OUT/gateway/fullchain.pem"

cat > "$OUT/install-windows.cmd" <<'EOF'
@echo off
rem Run as Administrator on each staff PC. Trusts only this hospital's name-constrained CA.
certutil -addstore -f Root "%~dp0ca\ca.crt"
EOF
cat > "$OUT/install-linux.sh" <<'EOF'
#!/bin/sh
# Run as root on each staff PC (Debian/Ubuntu). Trusts only this hospital's name-constrained CA.
set -e
install -m 0644 "$(dirname "$0")/ca/ca.crt" /usr/local/share/ca-certificates/nidan-site-ca.crt
update-ca-certificates
EOF
chmod 0755 "$OUT/install-linux.sh"
chmod 0600 "$OUT/ca/ca.key" "$OUT/gateway/privkey.pem"

openssl verify -CAfile "$OUT/ca/ca.crt" "$OUT/gateway/cert.pem" >/dev/null
echo "Site CA written to $OUT"
echo "  server : copy $OUT/gateway/{fullchain.pem,privkey.pem} to /etc/letsencrypt/live/<GATEWAY_DOMAIN>/"
echo "  PCs    : run install-windows.cmd / install-linux.sh with $OUT/ca/ca.crt next to it"
echo "  CA key : $OUT/ca/ca.key, keep OFFLINE; it is not needed on the server"
