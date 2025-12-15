# Nidan OpenMRS Distro

This folder contains the build context for the OpenMRS 3.x Backend and Frontend.

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

## 3. Configuration (`frontend/config-core_demo.json`)
The file `frontend/config-core_demo.json` controls the runtime behavior of the frontend (e.g., logos, primary colors, default locales, enabled extensions).

To apply changes:
1.  Edit the file.
2.  The Dockerfile copies it to the web root.
3.  Rebuild `openmrs-frontend`.
