# Nidan EHR – Complete System Context

**Purpose**: Use this document as context when starting a new agent session to reduce token consumption. Copy/paste relevant sections into the new session.

---

## 1. Repository Structure (Monorepo)

```
nidanhis/
├── nidan-docker/           # Docker orchestration (main entry point)
├── openmrs-backend/        # OpenMRS Java modules (OMODs)
├── openmrs-frontend/      # OpenMRS O3 SPA micro-frontends
├── OpenELIS-Global-2/      # Lab information system (Java)
├── odoo_19_addons/        # Odoo 19 custom addons (Python)
├── nidan-middleware/       # CIS + OIS (Java Spring Boot)
├── nidan-dhis2-integration/# DHIS2/Federal HMIS reporting (Python Flask)
└── nidan-proxy-image/     # Gateway config templates
```

---

## 2. Services & Architecture

| Service | Image/Context | Port | DB | Description |
|---------|---------------|------|-----|-------------|
| **gateway** | `./proxy` | 80, 443, 8443 | - | Nginx reverse proxy, SSL (certbot), routes to all apps |
| **openmrs-backend** | `./openmrs` | 8080 (internal) | PostgreSQL | OpenMRS 2.8 API (Tomcat) |
| **openmrs-frontend** | `./openmrs/frontend` | 80 (internal) | - | O3 SPA (static assets) |
| **openmrs-db** | postgres:15-alpine | 5433→5432 | - | OpenMRS database |
| **odoo** | trigonaltechnology/nidan_odoo | 8069 | PostgreSQL | ERP (billing, inventory, POS) |
| **odoo-db** | postgres:15 | - | - | Odoo database |
| **orthanc** | orthancteam/orthanc | 8042, 4242 | PostgreSQL | PACS (DICOM), OHIF viewer |
| **orthanc-db** | postgres:15 | - | - | Orthanc DB |
| **openelis** | OpenELIS-Global-2/Dockerfile | 8083→8080 | PostgreSQL | Lab backend |
| **openelis-db** | trigonaltechnology/openelis-global-2-database | 15432→5432 | - | Lab DB |
| **openelis-frontend** | OpenELIS-Global-2/frontend | 8085→80 | - | Lab UI |
| **nidan-cis** | trigonaltechnology/nidan-cis | 8081 | - | Clinical Integration Service (Kafka consumer) |
| **nidan-ois** | trigonaltechnology/nidan-ois | 8082 | - | Order Integration Service (Odoo webhooks) |
| **nidan-integration-db** | postgres:15 | - | - | Integration DB (CIS/OIS state, Debezium) |
| **nidan-kafka** | confluentinc/cp-kafka:7.5.0 | 9092 | - | Event streaming |
| **nidan-zookeeper** | confluentinc/cp-zookeeper:7.5.0 | 2181 | - | Kafka coordination |
| **nidan-dhis2** | trigonaltechnology/nidan-dhis2-integration | 5003→5000 | MySQL | DHIS2/Federal HMIS reporting |
| **nidan-dhis2-db** | mysql:8.0 | - | - | DHIS2 audit DB |
| **superset** | `./superset` | 8088 | - | BI/dashboards (connects to OpenMRS, OpenELIS, Odoo) |
| **keycloak** | quay.io/keycloak | - | - | Identity (optional) |

**URLs (via gateway)**:
- `/` – Dashboard (index.html)
- `/openmrs` – OpenMRS SPA
- `/odoo` – Odoo
- `/openelis` – OpenELIS frontend
- `/api/OpenELIS-Global/` – OpenELIS backend (context path; frontend expects this)
- `/orthanc-container` – Orthanc PACS
- `/auth` – Keycloak
- `/superset` – Superset

**Proxy notes**: Port 8443 terminates TLS for OpenELIS-Global (hardcoded in pre-built frontend). OpenELIS backend context: `SERVER_SERVLET_CONTEXT_PATH=/OpenELIS-Global`; gateway rewrites `/api/OpenELIS-Global/` → `openelis:8080/OpenELIS-Global/`.

---

## 3. OpenMRS Backend – Build & Modules

### 3.1 Docker Build (`nidan-docker/openmrs/Dockerfile`)

**Two-stage build**:
1. **content-clone** (Alpine): Clones content repo
   - `CONTENT_REPO_URL`: https://github.com/Trigonal-Technology/openmrs-content-referenceapplication-demo.git (or openmrs with `nidan` branch)
   - `CONTENT_REPO_REF`: main or nidan
   - `CONTENT_VERSION`: 1.8.0-nidan-SNAPSHOT
2. **dev** (openmrs/openmrs-core:2.8.x-dev-amazoncorretto-21):
   - Builds content package (`mvn install`), then distro (`mvn clean install -P no-demo`)
   - Copies WAR, modules, OWAs, config to `/openmrs/distribution/`
3. **Run stage**: Copies artifacts + **custom_modules/**

**Build profile**: `no-demo` (excludes referencedemodata, stockmanagement, billing). Use `OMRS_PROFILE=distro` for full demo.

### 3.2 Custom Modules (Pre-built OMODs)

Located in `nidan-docker/openmrs/custom_modules/`. **Not in git** (large binaries). Must be built and copied before `docker-compose build openmrs-backend`:

```bash
# From repo root
cd openmrs-backend/openmrs-module-attachments && mvn package -DskipTests && cp omod/target/*.omod ../../../nidan-docker/openmrs/custom_modules/
cd ../openmrs-module-communication && mvn package -DskipTests && cp omod/target/*.omod ../../../nidan-docker/openmrs/custom_modules/
cd ../openmrs-module-appointments && mvn package -DskipTests && cp omod/target/*.omod ../../../nidan-docker/openmrs/custom_modules/
# fhir2, bahmni-ipd, medication-administration, orderexpansion – same pattern
```

**Custom modules**: attachments, appointments, communication, fhir2, bahmni-ipd, medication-administration, orderexpansion.

**Local-first Maven**: POMs use `updatePolicy=never` for snapshot repos so `~/.m2/repository` is preferred. Build order: `fhir2` → `medication-administration` → `ipd`. Optional: `mvn -s openmrs-backend/settings-local-first.xml ...`.

### 3.3 Distro Modules (`distro/distro-no-demo.properties`)

- initializer, fhir2, webservices.rest, idgen, legacyui, addresshierarchy, patientdocuments, metadatamapping, openconceptlab
- queue, teleconsultation, cohort, reporting, reportingrest, calculation, htmlwidgets, ordertemplates, patientflags, o3forms
- authentication, emrapi, event, bedmanagement
- **content.referenceapplication** (1.4.0), **content.referenceapplication-demo** (1.8.0-nidan-SNAPSHOT)
- **Excluded**: referencedemodata, stockmanagement, billing

### 3.4 Database: PostgreSQL

- **openmrs-db**: PostgreSQL 15 Alpine
- **Init script**: `openmrs-db/init-openmrs-db.sql` – uuid-ossp, pg_trgm, hibernate_sequence
- **JDBC URL**: `jdbc:postgresql://openmrs-db:5432/openmrs?stringtype=unspecified`
- **Startup**: `startup-custom.sh` patches runtime properties (connection.url with `stringtype=unspecified`) for PostgreSQL

### 3.5 PostgreSQL-Specific Fixes

- **provider_attribute_type**: Use column `datatype` (not `datatype_classname`). OpenMRS `BaseAttributeType` maps `datatypeClassname` → `datatype`.
- **Troubleshooting**: See `nidan-docker/openmrs/TROUBLESHOOTING.md` for "Bad value for type long" (concept_class, patient_identifier_type) and metadata loading issues.

---

## 4. OpenMRS Frontend – SPA Build

**Context**: `nidan-docker/openmrs/frontend/`

**Build**:
```bash
docker run --rm -it -v $(pwd)/openmrs/frontend:/app -w /app node:20-alpine \
  /bin/sh -c "npm install -g openmrs@next && cp spa-build-config.json spa-assemble-config.json && npx openmrs assemble --manifest --mode config --config spa-assemble-config.json --target ./spa"
```

Or: `docker-compose build openmrs-frontend`

**Config**: `spa-assemble-config.json` – lists frontend modules (e.g. @openmrs/esm-*, @kenyaemr/*, @trigonal/*). Output: `./spa/` (index.html + assets).

**Source packages** (in `openmrs-frontend/`):
- openmrs-esm-patient-chart
- kenyaemr-esm-orders
- trigonal-esm-imaging-orders-app
- vite_react_shadcn_ts (dev)

---

## 5. OpenELIS (Lab System)

- **Backend**: Built from `OpenELIS-Global-2/Dockerfile`
- **Frontend**: `OpenELIS-Global-2/frontend/Dockerfile.prod`
- **DB**: PostgreSQL (clinlims)
- **Context path**: `/api/OpenELIS-Global/`
- **Volumes**: configuration, properties, ocl, logs, analyzer, odoo, certs
- **Tomcat**: `openelis/tomcat/server.xml` – HTTP on 8080

---

## 6. Odoo 19

- **Image (selectable via `ODOO_IMAGE`)**: two layers —
  - `trigonaltechnology/nidan_odoo:stable` — open-source base, built from `odoo_19_addons/packages/Dockerfile`; bakes open-source addons into `/mnt/extra-addons`.
  - `trigonaltechnology/nidan_odoo_pro:stable` — **private**, built from `nidan_odoo_extra_addons/packages/Dockerfile` (`FROM` the base); bakes closed-source addons (`nidan_commission_management`, `nidan_payment_management`) into `/mnt/private-addons`. Pushed to a private Docker Hub repo. Hospitals set `ODOO_IMAGE` to this. Build order: base first, then Pro.
- **Addons**: Baked into the image in prod (no host mount). For dev, `dev.docker-compose.yml` mounts `ODOO_EXTRA_ADDONS_HOST_PATH` → `/mnt/extra-addons` and `ODOO_PRIVATE_ADDONS_HOST_PATH` → `/mnt/private-addons`.
- **odoo.conf**: `addons_path = /usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons,/mnt/private-addons`
- **First-run auto-install**: entrypoint scans `ADDONS_INIT_DIRS` (default `/mnt/extra-addons`; Pro image sets `/mnt/extra-addons /mnt/private-addons`); Odoo resolves install order from each manifest's `depends`.
- **Key addons**: nidan_core, nidan_connector, nidan_pos_enhance, nidan_insurance_management, base_accounting_kit, l10n_np, nepal_address_hierarchy, image_capture_upload_widget; private: nidan_commission_management, nidan_payment_management
- **OIS webhook**: `NIDAN_OIS_URL`, `NIDAN_OIS_SECRET` for Odoo ↔ OIS

---

## 7. Nidan Middleware (CIS + OIS)

**CIS (Clinical Integration Service)**:
- Consumes OpenMRS data (REST + optional Debezium CDC)
- Publishes to Kafka
- Integrates with OpenELIS (lab orders via FHIR Task bundles)
- Integrates with Orthanc (radiology)
- Env: OPENMRS_BASE_URL, OPENELIS_FHIR_BASE_URL, ORTHANC_BASE_URL, KAFKA_BOOTSTRAP_SERVERS

**OIS (Order Integration Service)**:
- Consumes Kafka (order events)
- Odoo REST API + webhooks
- Lab catalog sync (OpenELIS → Odoo via OCL)
- Env: ODOO_BASE_URL, ODOO_DATABASE, KAFKA_BOOTSTRAP_SERVERS, NIDAN_OIS_SECRET

**Images**: trigonaltechnology/nidan-cis, trigonaltechnology/nidan-ois (pre-built). Optional local build: `./middleware` with Dockerfile.cis / Dockerfile.ois.

---

## 8. DHIS2 / Federal HMIS Integration

- **App**: nidan-dhis2 (Python Flask)
- **DB**: MySQL (nidan_dhis2)
- **Sources**: OpenMRS, OpenELIS, Odoo (direct DB connections)
- **Targets**: Federal HMIS Nepal, optional Provincial DHIS2
- **Config**: `./dhis2/mappings`, `./dhis2/connections`, `./dhis2/sql`
- **Source code**: `nidan-dhis2-integration/`

---

## 9. Superset (BI)

- **Build**: `./superset/Dockerfile`
- **Config**: `superset_config.py`, `init_superset_metadata.py`
- **Metadata**: `./superset/metadata/` (datasets YAML)
- **Connections**: Seeded from OPENMRS_DB_*, OPENELIS_DB_*, ODOO_DB_* env vars

---

## 10. Environment Variables (env.template → .env)

**Key groups**:
- OpenMRS: OPENMRS_DB_*, OMRS_* (in compose)
- OpenELIS: OPENELIS_DB_*, OPENELIS_DEFAULT_ADMIN_PASSWORD
- Odoo: ODOO_DB_*, ODOO_POSTGRES_*, NIDAN_OIS_URL, NIDAN_OIS_SECRET
- Integration: INTEGRATION_DB_*, KAFKA_*, CIS_*, OIS_*
- Orthanc: ORTHANC_DB_*, ORTHANC_USERNAME
- DHIS2: DHIS2_*, FEDERAL_HMIS_*, PROVINCIAL_DHIS2_*
- Superset: SUPERSET_SECRET_KEY, SUPERSET_LOAD_EXAMPLES

---

## 11. Build Commands Summary

```bash
# 1. Copy .env
cp env.template .env

# 2. Build custom OMODs and copy to custom_modules (see section 3.2)

# 3. Build OpenMRS backend (includes content clone + distro)
docker-compose build openmrs-backend

# 4. Build OpenMRS frontend
docker-compose build openmrs-frontend

# 5. Build Odoo (if custom image)
docker-compose build odoo

# 6. Build gateway (if nginx changed)
docker-compose build gateway

# 7. Build Superset
docker-compose build superset

# 8. Start all
docker-compose up -d
```

---

## 12. Data Flows

1. **Clinical**: OpenMRS (patient, encounters, orders) → CIS (Kafka) → OpenELIS (lab), Orthanc (radiology)
2. **Orders**: OpenMRS orders → CIS → Kafka → OIS → Odoo (POS, inventory)
3. **Lab results**: OpenELIS → OIS/FHIR → OpenMRS (via FHIR2 or REST)
4. **Reporting**: OpenMRS, OpenELIS, Odoo DBs → nidan-dhis2 → Federal HMIS
5. **BI**: Superset reads OpenMRS, OpenELIS, Odoo DBs directly

---

## 13. Key File Paths

| Purpose | Path |
|---------|------|
| Docker compose | nidan-docker/docker-compose.yml |
| OpenMRS Dockerfile | nidan-docker/openmrs/Dockerfile |
| OpenMRS distro props | nidan-docker/openmrs/distro/distro-no-demo.properties |
| Custom modules dir | nidan-docker/openmrs/custom_modules/ |
| OpenMRS DB init | nidan-docker/openmrs-db/init-openmrs-db.sql |
| Appointments Liquibase | openmrs-backend/openmrs-module-appointments/api/src/main/resources/liquibase.xml |
| Odoo addons (dev) | dev.docker-compose.yml mounts ODOO_EXTRA_ADDONS_HOST_PATH → /mnt/extra-addons |
| OpenELIS config | nidan-docker/openelis/volume/ |
| Proxy config | nidan-docker/proxy/nginx.conf |

---

## 14. Known Issues & Fixes

- **provider_attribute_type**: Use `datatype` column, not `datatype_classname` (Liquibase in appointments module).
- **PostgreSQL "Bad value for type long"**: concept_class, patient_identifier_type – see TROUBLESHOOTING.md; often needs fresh DB or content fix.
- **OpenMRS content**: Trigonal fork (CONTENT_REPO_REF=main) or openmrs with `nidan` branch; CONTENT_VERSION=1.8.0-nidan-SNAPSHOT for local build.
- **OpenELIS FHIR**: Use HTTPS gateway URL when CIS calls external OpenELIS; mount truststore if self-signed.
- **integration.md**: Mentions MariaDB for OpenMRS – outdated; OpenMRS now uses PostgreSQL.
