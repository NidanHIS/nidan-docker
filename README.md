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

## Getting Started

### 1. Build the Distribution
This will compile the Java modules for OpenMRS and build all containers.
```bash
docker-compose build
```
*Note: The first build of openmrs-backend may take 5-10 minutes to download Maven dependencies.*

### 2. Run the Stack
```bash
docker-compose up -d
```

### 3. Access
Navigate to [http://localhost](http://localhost).
You will be redirected to Keycloak for login.
*   **Default Admin**: `admin` / `admin`

## Development
To add a new OpenMRS module:
1.  Add the dependency to `openmrs/distro/pom.xml`.
2.  Rebuild: `docker-compose build openmrs-backend && docker-compose up -d openmrs-backend`.
