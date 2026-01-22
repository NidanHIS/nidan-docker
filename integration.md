# Nidan Integration Overview

This document describes how the Nidan stack integrates with OpenMRS, OpenELIS, and the Nidan middleware (CIS/OIS), and how to enable CDC (Debezium) and external ELIS connectivity.


---

## 1. Runtime Topology (High Level)

The `nidan-docker` stack composes the following logical subsystems:

- **OpenMRS**: backend (`openmrs-backend`), frontend (`openmrs-frontend`), and MariaDB (`openmrs-db`).
- **OpenELIS**: database (`openelis-db`), backend (`openelis`), FHIR server (`openelis-fhir`), and frontend (`openelis-frontend`).
- **Nidan Integration Middleware**:
  - **CIS** (`nidan-cis`): consumes clinical data (e.g. from OpenMRS) and publishes to Kafka.
  - **OIS** (`nidan-ois`): integrates with Odoo for order/result handling.
  - **Integration DB** (`nidan-integration-db`) + **Kafka/Zookeeper** (`nidan-kafka`, `nidan-zookeeper`).
- **Odoo**: application (`odoo`) and PostgreSQL (`odoo-db`) with custom addons mounted from the host.
- **Orthanc**: core and DB for imaging.
- **Keycloak**: identity provider.
- **Gateway**: edge proxy that fronts the above when used in front of a browser.

Most services are parameterized via the root `.env` living beside `docker-compose.yml`. The README describes baseline usage; this document focuses on integration‑specific behavior.

---

## 2. Environment Configuration – Integration‑Relevant Groups

Only the most relevant env groups for integration are listed here; see `env.template` for full set.

- **OpenMRS database and API**
  - `OPENMRS_DB_NAME`, `OPENMRS_DB_USER`, `OPENMRS_DB_PASSWORD`, `OPENMRS_DB_ROOT_PASSWORD`
  - `OPENMRS_DB_HOST` (typically `openmrs-db` inside this stack)
  - `OPENMRS_BASE_URL` (CIS/OpenELIS use this to call OpenMRS REST)

- **OpenELIS database and FHIR**
  - `OPENELIS_DB_NAME`, `OPENELIS_DB_USER`, `OPENELIS_DB_PASSWORD`
  - `OPENELIS_DEFAULT_ADMIN_PASSWORD`, `OPENELIS_TZ`
  - Optional FHIR SSL:
    - `SSL_TRUSTSTORE_PATH`, `SSL_TRUSTSTORE_PASSWORD`
    - `SSL_KEYSTORE_PATH`, `SSL_KEYSTORE_PASSWORD`

- **Integration DB and Kafka**
  - `INTEGRATION_DB_NAME`, `INTEGRATION_DB_USER`, `INTEGRATION_DB_PASSWORD`, `INTEGRATION_DB_HOST`, `INTEGRATION_DB_PORT`
  - `KAFKA_BROKER_ID`, `KAFKA_PORT`, `KAFKA_ZOOKEEPER_CONNECT`, and related replication/min‑ISR flags.

- **CIS/OIS runtime**
  - `CIS_PORT`, `CIS_KAFKA_GROUP_ID`
  - `OIS_PORT`, `OIS_KAFKA_GROUP_ID`
  - `OPENMRS_ENABLED`, `OPENMRS_BASE_URL`, `OPENMRS_USERNAME`, `OPENMRS_PASSWORD`, identifier/location UUIDs.
  - `ORTHANC_ENABLED`, `ORTHANC_BASE_URL`, `ORTHANC_USERNAME`, `ORTHANC_PASSWORD`.
  - `OPENELIS_ENABLED`, `OPENELIS_BASE_URL`, and  `OPENELIS_FHIR_BASE_URL` / `OPENELIS_ORDER_BUNDLE_PATH` for ELIS integration.

- **Odoo / OIS integration**
  - `ODOO_DB_*`, `ODOO_POSTGRES_*`, `ODOO_ADMIN_*`.
  - `ODOO_EXTRA_ADDONS_HOST_PATH` (host path for custom addons, including the Nidan connector).
  - `NIDAN_OIS_URL`, `NIDAN_OIS_SECRET` for the Odoo ←→ OIS webhook pairing.

---

## 3. OpenMRS + Debezium / CDC

The stack exposes a path to run Debezium‑style change data capture (CDC) against the OpenMRS database. This is disabled by default and can be enabled when needed.

### 3.1 Database Runtime Requirements

The OpenMRS DB container (`openmrs-db`) is a MariaDB 10.11 instance. By default, the compose file uses a conservative `mysqld` command:

```yaml
openmrs-db:
  image: mariadb:10.11.7
  command: "mysqld --character-set-server=utf8mb4 --collation-server=utf8mb4_general_ci --sql-mode=''"
```

For CDC, the DB must be configured with a unique `server-id` and row‑based binary logging. The compose file documents an alternative command (commented out) that is safe for Debezium:

```yaml
# Configure MariaDB with utf8mb4 and row-based binlog (unique server-id, relaxed SQL mode)
# for Debezium/OpenMRS CDC. Apply the same flags if OpenMRS DB runs outside this stack.
#command: "mysqld --character-set-server=utf8mb4 --collation-server=utf8mb4_general_ci --sql-mode='' \
#  --server-id=2749 --log-bin=mysql-bin --binlog_format=ROW --binlog_row_image=FULL"
```

If OpenMRS is hosted externally, the same effective flags must be configured on that instance.

### 3.2 CIS Configuration for Debezium

The CIS container (`nidan-cis`) embeds Debezium support behind env flags. The compose file includes the following commented configuration:

```yaml
# Debezium Embedded (CIS) – variable controls for Debezium
# - DEBEZIUM_ENABLED=${DEBEZIUM_ENABLED:-false}
# - DEBEZIUM_DB_USER=${DEBEZIUM_DB_USER:-root}
# - DEBEZIUM_DB_PASSWORD=${DEBEZIUM_DB_PASSWORD:-root}
```

To enable Debezium‑based CDC from OpenMRS:

- Ensure the OpenMRS DB (internal or external) is running with row‑based binlog and a distinct `server-id` as described above.
- Provide a DB user for Debezium with appropriate privileges to read binlogs.
- Set `DEBEZIUM_ENABLED=true` and corresponding `DEBEZIUM_DB_USER` / `DEBEZIUM_DB_PASSWORD` in the environment passed to `nidan-cis`.


---

## 4. CIS → OpenELIS Connectivity

CIS can integrate with OpenELIS in two primary modes: in‑stack ELIS over HTTP and external ELIS over HTTPS.

### 4.1 In‑Stack OpenELIS (HTTP, Default)

When `openelis-db`, `openelis`, and `openelis-fhir` are started from this compose, CIS can target the in‑stack FHIR endpoint.

Relevant section from `nidan-cis` service definition:

```yaml
# OpenELIS
- OPENELIS_ENABLED=${OPENELIS_ENABLED:-false}
- OPENELIS_BASE_URL=${OPENELIS_BASE_URL:-http://openelis-fhir:8080/fhir}
# Uncomment for FHIR bundle-based order routing
# - OPENELIS_FHIR_BASE_URL=${OPENELIS_FHIR_BASE_URL:-http://openelis-fhir:8080}
# - OPENELIS_ORDER_BUNDLE_PATH=${OPENELIS_ORDER_BUNDLE_PATH:-/fhir/tasks}
- OPENELIS_USERNAME=${OPENELIS_USERNAME:-admin}
- OPENELIS_PASSWORD=${OPENELIS_PASSWORD:-adminADMIN!}
```

Typical configuration when using the in‑stack ELIS:

- Keep `OPENELIS_BASE_URL` at the internal `openelis-fhir` URL.
- Set `OPENELIS_ENABLED=true` in the environment to allow CIS to call ELIS.
- Optionally configure `OPENELIS_FHIR_BASE_URL` and `OPENELIS_ORDER_BUNDLE_PATH` for Task‑based lab orders if required by the integration profile.

Communication in this mode is intra‑cluster HTTP; a truststore is not required for CIS → ELIS.

### 4.2 External OpenELIS over HTTPS

When ELIS is hosted outside this compose (for example, behind an HTTPS reverse proxy), CIS must trust the ELIS TLS certificate and target the external URL.

The `nidan-cis` service documents the required wiring as commented guidance:

```yaml
# To call an EXTERNAL HTTPS OpenELIS:
# 1) Map the ELIS hostname to your ELIS IP (if DNS is not authoritative)
# extra_hosts:
#   - "elis.openelis-global.org:192.168.1.74"
# 2) Mount a truststore built from the ELIS TLS certificate
# volumes:
#   - ./openelis-truststore.p12:/etc/ssl/certs/openelis-truststore.p12:ro
...
# - "JAVA_TOOL_OPTIONS=-Djavax.net.ssl.trustStore=/etc/ssl/certs/openelis-truststore.p12 -Djavax.net.ssl.trustStorePassword=${OPENELIS_TRUSTSTORE_PASSWORD}"
```

At a high level, the steps are:

1. **Export ELIS’s TLS certificate (or CA) and create a PKCS#12 truststore**
   - Export the ELIS server/CA cert to `elis.crt`.
   - Import it into a PKCS#12 truststore `openelis-truststore.p12` with a chosen password.

2. **Place `openelis-truststore.p12` in the `nidan-docker` directory** (so the relative volume mount works).

3. **Update the CIS service configuration**
   - Uncomment `extra_hosts` if DNS does not already resolve the ELIS hostname to the correct address.
   - Uncomment the `volumes` entry mounting `openelis-truststore.p12` into `/etc/ssl/certs/`.
   - Provide a `JAVA_TOOL_OPTIONS` environment entry that points to the mounted truststore and references a password held in `OPENELIS_TRUSTSTORE_PASSWORD`.

4. **Set ELIS integration env values for CIS** (in the environment passed to `nidan-cis`):
   - `OPENELIS_ENABLED=true`.
   - `OPENELIS_BASE_URL` and, if applicable, `OPENELIS_FHIR_BASE_URL` / `OPENELIS_ORDER_BUNDLE_PATH` pointing to the external HTTPS endpoints.
   - `OPENELIS_USERNAME` / `OPENELIS_PASSWORD` matching the ELIS credentials for this integration.
   - `OPENELIS_TRUSTSTORE_PASSWORD` matching the truststore password.

After these steps, CIS will establish HTTPS connections to the external ELIS instance using the mounted truststore.

---

## 5. Odoo and Middleware Integration

The Odoo application and the Nidan middleware (OIS) are wired via environment variables and a custom addons mount:

- `odoo` uses:
  - `HOST`, `USER`, `PASSWORD`, `PORT`, `ODOO_DB_NAME` for DB access.
  - `ODOO_ADMIN_EMAIL`, `ODOO_ADMIN_PASSWORD` for initial administration.
  - `NIDAN_OIS_URL` and `NIDAN_OIS_SECRET` for outbound communication to OIS.
  - `ODOO_EXTRA_ADDONS_HOST_PATH` to mount a host directory at `/mnt/extra-addons`.

- `nidan-ois` uses:
  - `INTEGRATION_DB_URL`, `INTEGRATION_DB_USER`, `INTEGRATION_DB_PASSWORD` to reach the integration DB.
  - `KAFKA_BOOTSTRAP_SERVERS` / `OIS_KAFKA_GROUP_ID` for Kafka.
  - `ODOO_BASE_URL`, `ODOO_DATABASE`, `ODOO_USERNAME`, `ODOO_PASSWORD`, and POS defaults for Odoo integration.

This arrangement allows the Nidan Odoo connector (and other custom addons) to be developed and mounted from the host while keeping the service wiring stable across environments.

---

## 6. OpenMRS Reference Application Distro

The `openmrs-distro-referenceapplication` repository provides a separate way to build and run the OpenMRS Reference Application outside the Nidan gateway, if needed.

### 6.1 Dockerfile (Two‑Stage Build)

The `Dockerfile` is a conventional two‑stage build:

- **Dev build stage** (`FROM openmrs/openmrs-core:2.8.x-dev-amazoncorretto-21 AS dev`):
  - Copies `pom.xml` and the `distro/` module.
  - Runs Maven with the distro profile to produce an SDK distro.
  - Copies the built artifacts into `/openmrs/distribution/`:
    - `openmrs.war`
    - `openmrs-distro.properties`
    - `openmrs_modules`, `openmrs_owas`, `openmrs_config`
  - Performs `mvn ... clean` to discard build‑time artifacts.

- **Run stage** (`FROM openmrs/openmrs-core:2.8.x-amazoncorretto-21`):
  - Copies the built `openmrs.war` and distro content from the dev stage into the runtime image under `/openmrs/distribution/`.

This produces a self‑contained OpenMRS runtime image that can be deployed either within the Nidan stack or standalone.

### 6.2 Frontend nginx Configuration

`frontend/nginx.conf` is configured to serve the Reference Application SPA under `/openmrs/spa/` and proxy backend API calls to the corresponding OpenMRS backend container:

- SPA hosting:

  ```nginx
  location /openmrs/spa/ {
    alias /usr/share/nginx/html/;
    # static caching and SPA routing rules
  }
  ```

- Backend proxy:

  ```nginx
  # Proxy OpenMRS backend through the frontend container.
  location /openmrs/ {
    proxy_pass http://backend:8080/openmrs/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
  ```

- Root redirect:

  ```nginx
  location = / {
    return 301 /openmrs/spa/index.html;
  }
  ```

This layout allows the OpenMRS Reference Application to be served without routing HTTP traffic through the Nidan/default gateway: the frontend container terminates HTTP for both the SPA and the `/openmrs/` backend path and proxies to the backend container named `backend`.

---

## 7. Notes on Running the Stack

For standard development usage, the existing `README.md` in `nidan-docker` remains the primary source of truth for starting and stopping the stack. This document is intended to complement it by describing integration‑specific behavior, expected database runtime configuration, and the options available for Debezium and external OpenELIS connectivity.

---

## 8. CIS TLS Truststore for Gateway (In‑Stack HTTPS)

When CIS calls OpenELIS through the in‑stack gateway over HTTPS (for example, `https://gateway/api/OpenELIS-Global/...`), Java’s `HttpClient` must trust the gateway’s TLS certificate. This section summarizes the steps to create a truststore from the gateway certificate and wire it into the `nidan-cis` service.

### 8.1 Create a Truststore from the Gateway Certificate

1. **Export the gateway certificate from the host (one‑time)**

   From the `nidan-docker` directory on the host:

   ```bash
   # From nidan-docker directory on host
   openssl s_client -showcerts -connect localhost:443 </dev/null 2>/dev/null \
     | openssl x509 -outform PEM > gateway.pem
   ```

2. **Build a PKCS#12 truststore from the exported certificate**
  **Run this command to remove the existing truststore:** 
  ```bash
  rm -rf gateway-truststore.p12
  ```

   ```bash
   keytool -importcert \
     -keystore gateway-truststore.p12 \
     -storetype PKCS12 \
     -storepass gatewayTrust123 \
     -alias gateway \
     -file gateway.pem \
     -noprompt
   ```

3. **Place `gateway-truststore.p12` alongside `docker-compose.yml` (or an appropriate subdirectory)**

   This ensures the relative volume mount used by `nidan-cis` resolves correctly.

### 8.2 Mount the Truststore into `nidan-cis`

In `docker-compose.yml`, under the `nidan-cis` service, mount the truststore into the container:

```yaml
nidan-cis:
  volumes:
    - ./gateway-truststore.p12:/etc/ssl/certs/gateway-truststore.p12:ro
```

### 8.3 Configure Java to Use the Truststore

Still under the `nidan-cis` service in `docker-compose.yml`, configure `JAVA_TOOL_OPTIONS` so that Java’s HTTP client uses the mounted truststore. In development, hostname verification can be relaxed to account for `CN=localhost` while calling `https://gateway/...`:

```yaml
- JAVA_TOOL_OPTIONS=-Djavax.net.ssl.trustStore=/etc/ssl/certs/gateway-truststore.p12 -Djavax.net.ssl.trustStorePassword=gatewayTrust123 -Djdk.internal.httpclient.disableHostnameVerification=true
```

- The `trustStore` / password pair ensures Java trusts the self‑signed gateway certificate.
- `-Djdk.internal.httpclient.disableHostnameVerification=true` is a development‑time workaround for the mismatch between `CN=localhost` on the certificate and the internal hostname `gateway` used inside the Docker network.

In a more production‑oriented deployment, you would instead issue a certificate whose `CN` or `subjectAltName` includes `gateway` (for example, `CN=gateway` or `subjectAltName=DNS:gateway`) and omit the hostname‑disabling flag.

---

## 9. Using a Local OpenELIS WAR in Development

For development and rapid iteration on the OpenELIS backend, you can override the WAR bundled in the Docker image by mounting a locally built `OpenELIS-Global.war` into the `openelis` service in `dev.docker-compose.yml`.


```yaml
openelis:
  volumes:
    - ./openelis/volume/properties/common.properties:/run/secrets/common.properties:ro
    - ./openelis/volume/certs:/etc/openelis-global:ro
    - ./OpenELIS-Global.war:/usr/local/tomcat/webapps/OpenELIS-Global.war:ro
```

- This override is intended for development; in more stable or production‑like environments you would typically rely on a tested image that already contains the correct WAR.