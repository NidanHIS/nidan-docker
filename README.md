# NidanEHR Docker Distribution

This repository contains the containerized architecture for **NidanEHR**, orchestrating OpenMRS 3.0, Odoo, OpenELIS, Orthanc, and Keycloak behind a central Gateway.

## Architecture

| Service | internal Host | URL Path | Description |
|---|---|---|---|
| **Gateway** | `gateway` | `/` | Nginx Reverse Proxy & SSL Termination |
| **OpenMRS Frontend** | `openmrs-frontend` | `/openmrs` | O3 SPA Assets (static) |
| **OpenMRS Backend** | `openmrs-backend` | `/openmrs/ws` | Java API (Tomcat) |
| **Odoo 19** | `odoo` | `/odoo` | ERP System |
| **OpenELIS Backend** | `openelis` | `/openelis` | Lab System Backend |
| **OpenELIS FHIR** | `openelis-fhir` | `/openelis-fhir` | Lab System FHIR API |
| **OpenELIS Frontend** | `openelis-frontend` | `/openelis-frontend` | Lab System Frontend |
| **Orthanc** | `orthanc` | `/orthanc-container` | PACS Server with OHIF Viewer |
| **Keycloak** | `keycloak` | `/auth` | Identity Management |

## Directory Structure

*   `openmrs/`: **The Core Distro**. Contains the Maven project `pom.xml` to build the WAR and the Dockerfile that layers it into Tomcat.
*   `odoo/`: Odoo Docker context with custom `addons/`.
*   `proxy/`: Nginx configuration for the main gateway.
*   `keycloak/`: Realm imports and themes.
*   `_backup_legacy/`: Archived legacy Bahmni-based configuration (can be removed).
*   `docker-compose.yml`: Main orchestration file.

## Build & Deployment Instructions

### Prerequisites
1. Copy `.env.example` to `.env` and customize the environment variables for your deployment:
   ```bash
   cp .env.example .env
   # Edit .env with your specific values
   ```

### 1. OpenMRS 3.0 Frontend (SPA)
The frontend is built using `npx openmrs assemble` to create a custom distribution of micro-frontends.

**Option A: Manual Build (Recommended for Development/Debugging)**
Run this if you want to see the build process output or "own" the build artifact on your host machine.
```bash
docker run --rm -it \
  -v $(pwd)/openmrs/frontend:/app \
  -w /app \
  node:20-alpine \
  /bin/sh -c "npm install -g openmrs@next && cp spa-build-config.json spa-assemble-config.json && npx openmrs assemble --manifest --mode config --config spa-assemble-config.json --target ./spa"
```
*   This creates a `./openmrs/frontend/spa` directory with `index.html` and assets.

**Option B: Docker Compose Build**
Standard deploy. Note: This can take 5-10 minutes on the first run as it downloads ~50 modules.
```bash
docker-compose build openmrs-frontend
```

### 2. OpenMRS Backend (API)
Compiles the custom distro using Maven and layers it into Tomcat.
```bash
docker-compose build openmrs-backend
```
*   **Source**: `openmrs/distro/pom.xml` defines the modules.
*   **Verification**: Check logs with `docker-compose logs -f openmrs-backend`.

### 3. Odoo 19
Builds the Odoo image with custom addons.
```bash
docker-compose build odoo
```
*   Add custom modules to `odoo/addons/`.

### 4. Nginx Gateway
Rebuild the proxy if you change `nginx.conf` or the dashboard `index.html.template`.
```bash
docker-compose build gateway
```

### 5. Orthanc (PACS Server)
Orthanc uses the official Orthanc Docker image with custom configuration and OHIF plugin.
```bash
# No build needed - uses pre-built image
# Configuration is in orthanc/orthanc.json
# OHIF viewer is integrated via Orthanc plugin
```

### 6. OpenELIS (Lab System)
OpenELIS uses pre-built images from I-TECH. Configuration is managed through environment variables and volume mounts.
```bash
# No build needed - uses pre-built images
# Configuration files are in openelis/volume/
```

### 8. Keycloak (Identity Management)
Keycloak uses the official Quay.io image with realm imports.
```bash
# No build needed - uses pre-built image
# Realm configuration is in keycloak/imports/
```

---

## Running the Stack
Start all services in detached mode:
```bash
docker-compose up -d
```
*   **Access**: [http://localhost](http://localhost) (Service Dashboard)
*   **OpenMRS**: [http://localhost/openmrs](http://localhost/openmrs)
*   **Odoo**: [http://localhost:8069](http://localhost:8069)

