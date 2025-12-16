# OpenELIS Integration (Nidan)

## Environment variables (add to your `.env`)
- `GATEWAY_DOMAIN` — main public domain (used by gateway TLS)
- `OPENELIS_DOMAIN` — public domain for OpenELIS (must match DNS)
- `OPENELIS_LETSENCRYPT_EMAIL` — email for Let's Encrypt/Certbot
- `OPENELIS_DB_NAME` — e.g. `clinlims`
- `OPENELIS_DB_USER` — e.g. `clinlims`
- `OPENELIS_DB_PASSWORD` — DB password
- `OPENELIS_DEFAULT_ADMIN_PASSWORD` — optional, default `adminADMIN!`
- `OPENELIS_TZ` — optional, default `UTC`
- `OPENELIS_CONTEXT_PATH` — context path for OpenELIS backend (like Spring Boot `server.servlet.context-path`), default `/api/OpenELIS-Global`

## Building the Backend Image

### Multi-Arch Build (AMD64 and ARM64)

The OpenELIS backend image supports multi-architecture builds for both AMD64 and ARM64 platforms.

**Prerequisites:**
- Docker Buildx must be installed and enabled
- Build context must be set to the parent directory (`nidanhis`) to access `OpenELIS-Global-2` source

**Build Command:**
```bash
cd nidan-docker/openelis
./build-multiarch.sh
```

Or with custom image name and tag:
```bash
IMAGE_NAME=your-registry/openelis-global-2 IMAGE_TAG=v1.0.0 ./build-multiarch.sh
```

The build script will:
1. Create or use a buildx builder named `openelis-multiarch-builder`
2. Build the image for both `linux/amd64` and `linux/arm64` platforms
3. Push the multi-arch manifest to the specified registry

**Note:** The Dockerfile expects the build context to be the parent directory (`nidanhis`) so it can access the `OpenELIS-Global-2` source code. The build script handles this automatically.

### Context Path Configuration

The OpenELIS backend supports configurable context path through the `SERVER_SERVLET_CONTEXT_PATH` environment variable (or `OPENELIS_CONTEXT_PATH` in docker-compose), similar to Spring Boot's `server.servlet.context-path` property.

**Default:** `/api/OpenELIS-Global` (maintains backward compatibility)

**Example:** To set a custom context path:
```bash
# In docker-compose.yml or .env file
OPENELIS_CONTEXT_PATH=/openelis/api
```

The context path is automatically configured in:
- Tomcat server.xml (via entrypoint script)
- Nginx gateway configuration (via environment variable substitution)

Make sure the `OPENELIS_CONTEXT_PATH` in docker-compose matches between the `openelis` service and `gateway` service.

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


