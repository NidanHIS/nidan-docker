# Nidan Middleware

This directory contains the Docker build context for the Nidan Integration Middleware services (CIS and OIS).

## Structure

```
middleware/
├── Dockerfile.cis          # Dockerfile for Clinical Integration Service
├── Dockerfile.ois          # Dockerfile for Odoo Integration Service
├── cis/
│   └── target/
│       └── nidan-cis-0.1.0-SNAPSHOT.jar    # Pre-built JAR (populated during build)
└── ois/
    └── target/
        └── nidan-ois-0.1.0-SNAPSHOT.jar    # Pre-built JAR (populated during build)
```

## Building the Middleware

The middleware JARs must be built before building the Docker images. 

### Option 1: Build from Source (Recommended for Development)

1. Navigate to the middleware source directory:
   ```bash
   cd ../nidan-middleware/integration
   ```

2. Build the JARs:
   ```bash
   mvn clean package -DskipTests
   ```

3. Copy the JARs to this directory:
   ```bash
   mkdir -p cis/target ois/target
   cp cis/target/nidan-cis-0.1.0-SNAPSHOT.jar ../nidan-docker/middleware/cis/target/
   cp ois/target/nidan-ois-0.1.0-SNAPSHOT.jar ../nidan-docker/middleware/ois/target/
   ```

### Option 2: Use Pre-built Images

If you've already built the images elsewhere, you can pull them:

```bash
docker pull nidan/cis:0.1.0
docker pull nidan/ois:0.1.0
```

Then update `docker-compose.yml` to use the images directly instead of building.

## Docker Compose Integration

The `docker-compose.yml` in the parent directory builds and runs both services:

- **CIS** (Clinical Integration Service) - Port 8081
- **OIS** (Odoo Integration Service) - Port 8082

Both services are automatically configured with:
- PostgreSQL database connection
- Kafka broker connection
- OpenMRS, Odoo, Orthanc integration settings
- Environment variables from `.env` file

## Services

### CIS (Clinical Integration Service)

- **Port**: 8081
- **Purpose**: Handles OpenMRS, OpenELIS, and Orthanc integrations
- **Health Check**: `http://localhost:8081/actuator/health`

### OIS (Odoo Integration Service)

- **Port**: 8082
- **Purpose**: Handles Odoo ERP integrations
- **Health Check**: `http://localhost:8082/actuator/health`

