# NidanEHR Docker Distribution

This repository contains the containerized architecture for **NidanEHR**, orchestrating OpenMRS 3.0, Odoo, OpenELIS, Orthanc, and Keycloak behind a central Gateway.

## Architecture

| Service | internal Host | URL Path | Description |
|---|---|---|---|
| **Gateway** | `gateway` | `/` | Nginx Reverse Proxy & SSL Termination |
| **OpenMRS Frontend** | `openmrs-frontend` | `/openmrs` | O3 SPA Assets (static) |
| **OpenMRS Backend** | `openmrs-backend` | `/openmrs/ws` | Java API (Tomcat) |
| **Odoo 19** | `odoo` | `/odoo` | ERP System |
| **OpenELIS** | `openelis` | `/openelis` | Lab System |
| **Orthanc** | `orthanc` | `/orthanc` | PACS Server |
| **Keycloak** | `keycloak` | `/auth` | Identity Management |

## Directory Structure

*   `openmrs/`: **The Core Distro**. Contains the Maven project `pom.xml` to build the WAR and the Dockerfile that layers it into Tomcat.
*   `odoo/`: Odoo Docker context with custom `addons/`.
*   `proxy/`: Nginx configuration for the main gateway.
*   `keycloak/`: Realm imports and themes.
*   `dockerc-compose.yml`: Main orchestration file.

## Build & Deployment Instructions

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
Rebuild the proxy if you change `nginx.conf` or the dashboard `index.html`.
```bash
docker-compose build gateway
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

