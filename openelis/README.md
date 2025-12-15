# OpenELIS Integration (Nidan)

## Environment variables (add to your `.env`)
- `GATEWAY_DOMAIN` — main public domain (used by gateway TLS)
- `OPENELIS_DOMAIN` — public domain for OpenELIS (must match DNS)
- `OPENELIS_LETSENCRYPT_EMAIL` — email for Let’s Encrypt/Certbot
- `OPENELIS_DB_NAME` — e.g. `clinlims`
- `OPENELIS_DB_USER` — e.g. `clinlims`
- `OPENELIS_DB_PASSWORD` — DB password
- `OPENELIS_DEFAULT_ADMIN_PASSWORD` — optional, default `adminADMIN!`
- `OPENELIS_TZ` — optional, default `UTC`

## One-time certificate issuance
Certbot container renews automatically, but you must issue the first cert:
```bash
docker-compose run --rm openelis-certbot certonly \
  --webroot -w /var/www/certbot \
  -d $OPENELIS_DOMAIN \
  --email $OPENELIS_LETSENCRYPT_EMAIL \
  --agree-tos --no-eff-email
```
Ensure port 80 of `$OPENELIS_DOMAIN` routes to the gateway.

## Keycloak realm import
Place the OpenELIS realm export JSON into `openelis/keycloak/`.
Keycloak mounts this at `/opt/keycloak/data/import/openelis` and imports on startup.

## Start / rebuild
```bash
docker-compose build gateway openelis openelis-proxy
docker-compose up -d gateway openelis openelis-proxy openelis-db openelis-certbot
```

## Routing
- HTTPS terminated at gateway using certbot-issued certs.
- `/openelis/` proxied to `openelis-proxy`, which forwards to OpenELIS backend.


