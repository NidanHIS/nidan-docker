#!/bin/sh
# Prints "on" when the gateway may send HSTS, else "off" (backlog KC-12-02, spec rule 13).
#   hsts-decide.sh DOMAIN CERT.pem        (GATEWAY_HSTS=on|off overrides; default auto)
# auto = on only for a DNS name (not an IP, not localhost) whose certificate is publicly issued: not
# self-signed and not from the Nidan site CA (scripts/site-ca.sh, issuer O = Nidan EHR site). HSTS on a
# bare-IP or LAN-CA site would lock out any PC that later lacks the CA, with no way to click through.
case "${GATEWAY_HSTS:-auto}" in on) echo on; exit 0 ;; off) echo off; exit 0 ;; esac
DOMAIN="$1"; CERT="$2"
if echo "$DOMAIN" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}$|^localhost$|:'; then echo off; exit 0; fi
[ -f "$CERT" ] || { echo off; exit 0; }
issuer="$(openssl x509 -noout -issuer -nameopt RFC2253 -in "$CERT" 2>/dev/null | sed 's/^issuer=//')"
subject="$(openssl x509 -noout -subject -nameopt RFC2253 -in "$CERT" 2>/dev/null | sed 's/^subject=//')"
if [ -z "$issuer" ] || [ "$issuer" = "$subject" ]; then echo off; exit 0; fi
case "$issuer" in *"O=Nidan EHR site"*) echo off; exit 0 ;; esac
echo on
