# Nidan OpenMRS Distro

This folder contains the build context for the OpenMRS 3.x Backend and Frontend.

## Production Build (Option B)

The Dockerfile performs a full custom build from source:
1. **Content**: Cloned from `Trigonal-Technology/openmrs-content-referenceapplication-demo` (override with `CONTENT_REPO_URL`, `CONTENT_REPO_REF`)
2. **Distro**: `distro/` controls backend modules via `distro-no-demo.properties`
3. **Custom modules**: `custom_modules/` provides Nidan OMODs (attachments, appointments, medication-administration, nidancore, etc.)

### Prerequisites

Populate `custom_modules/` with `.omod` files before building (see `custom_modules/README.md`):

```bash
# From repo root
cd openmrs-backend/openmrs-module-medication-administration && mvn package -DskipTests && cp omod/target/*.omod ../../../nidan-docker/openmrs/custom_modules/
# Repeat for attachments, appointments, nidancore, etc.
```

### Build

```bash
cd nidan-docker
docker-compose build openmrs-backend
docker-compose up -d openmrs-backend
```

**Local-first Maven**: POMs use `updatePolicy=never` for snapshot repos so `~/.m2/repository` is preferred over remote. Build custom modules in order: `fhir2` → `medication-administration` → `ipd`. Optional: `mvn -s openmrs-backend/settings-local-first.xml ...`.

**If Maven dependency resolution fails** (e.g. `openmrs.jfrog.io:443 failed to respond`):
- Retry the build (OpenMRS Maven repo can have transient issues)
- Try with host network: `docker build --network=host -f openmrs/Dockerfile ./openmrs`
- Check [OpenMRS status](https://status.openmrs.org/) for repo outages

### Build args

| ARG | Default | Description |
|-----|---------|-------------|
| CONTENT_REPO_URL | Trigonal-Technology/... | Content repo URL |
| CONTENT_REPO_REF | main | Branch or tag |
| CONTENT_VERSION | 1.8.0-nidan-SNAPSHOT | Content package version |
| CACHE_BUST | — | Set to invalidate cache |

## 1. Backend Customization (`distro/pom.xml`)

To add **Custom Backend Modules** (Java/OMODs):
1.  Open `distro/pom.xml`.
2.  Add your module dependency in the `<dependencies>` section:
    ```xml
    <dependency>
        <groupId>com.mycompany</groupId>
        <artifactId>my-module-omod</artifactId>
        <version>1.0.0</version>
    </dependency>
    ```
3.  Rebuild:
    ```bash
    docker-compose build openmrs-backend
    docker-compose up -d openmrs-backend
    ```

**Note**: If your module is not in the OpenMRS public repository, you can:
*   Add a standard `<repository>` entry in the `pom.xml`.
*   OR manually place the `.omod` file in `distro/modules/` (not recommended for reproducible builds).

## 2. Frontend Customization (`frontend/spa-build-config.json`)

To add **Custom Frontend Modules** (Microfrontends):
1.  Open `frontend/spa-build-config.json`.
2.  Add your module to the `libraries` object:
    ```json
    "libraries": {
        "@openmrs/esm-framework": "next",
        "my-custom-app": "^1.0.0" 
    }
    ```
3.  Rebuild:
    ```bash
    docker-compose build openmrs-frontend
    docker-compose up -d openmrs-frontend
    ```

**Nepali calendar (default)**: `frontend/Dockerfile` assembles `@nidan/esm-nepali-calendar` and patches date pickers + read-only date formatting. **Vanilla upstream SPA** (no BS calendar): `docker build -f openmrs/frontend/Dockerfile.vanilla openmrs/frontend` using `spa-assemble-config.vanilla.json`.

## 3. Configuration (`frontend/config-core_demo.json`)
The file `frontend/config-core_demo.json` controls the runtime behavior of the frontend (e.g., logos, primary colors, default locales, enabled extensions).

To apply changes:
1.  Edit the file.
2.  The Dockerfile copies it to the web root.
3.  Rebuild `openmrs-frontend`.

## 4. Database: MySQL vs PostgreSQL

OpenMRS uses **MySQL/MariaDB** by default. Set `OPENMRS_DB_ROOT_PASSWORD` in `.env`. PostgreSQL is kept as `openmrs-db-postgres` (profile `openmrs-postgres`); run with `docker-compose --profile openmrs-postgres up` to start it.

## 5. Local Content Build (PostgreSQL fixes)

The content repo `openmrs-content-referenceapplication-demo` includes **PostgreSQL liquibase fixes** in `configuration/liquibase/liquibase.xml`. These run first (LIQUIBASE is the Initializer's first domain) and fix "Bad value for type long" errors.

To build with **local content** (your edits to the content repo):

```bash
cd nidan-docker
docker-compose -f docker-compose.yml -f docker-compose.openmrs-local-content.yml build openmrs-backend
docker-compose -f docker-compose.yml -f docker-compose.openmrs-local-content.yml up -d openmrs-backend
```

Requires: `openmrs-backend` and `nidan-docker` as siblings under the same repo root.

## 6. Troubleshooting: Metadata / Initializer Not Loading

If OpenMRS starts but **no concepts, locations, or other metadata** appear, or it gets stuck during data insertion:

### 1. Run diagnostic (inside container)

```bash
docker exec -it nidan-openmrs-backend /openmrs/check-initializer.sh
```

Verifies that `configuration/` exists in the data dir and has files.

### 2. Fresh start (nuclear option)

Corrupted volume state can block Initializer. Do a full reset:

```bash
docker-compose down
docker volume rm nidan-docker_openmrs-data 2>/dev/null || true
docker-compose build --no-cache openmrs-backend
docker-compose up -d openmrs-backend
```

Then tail logs: `docker logs -f nidan-openmrs-backend` and wait for "OpenMRS config loading process completed" from Initializer.

### 3. PostgreSQL "Bad value for type long"

Use the local content build (above) so the liquibase fixes in the content repo are included. Then do a **fresh database**:

```bash
docker-compose down -v
docker-compose -f docker-compose.yml -f docker-compose.openmrs-local-content.yml up -d openmrs-backend
```

### 4. Configuration volume

The `./configuration:/openmrs/data/configuration` mount is **commented out** by default. Config comes from the baked-in image.

### 5. OCL concepts not loading

- **Checksum skip**: When using a mounted config, the startup script clears OCL checksums so concepts reload every time (avoids skip after DB reset).
- **Module version**: The distro uses Open Concept Lab 2.4.0 for compatibility with the Initializer's OCL loader.
