# Adding OpenMRS Modules

## Current Status

The build is now working with essential modules. Some modules were commented out because their specified versions don't exist in the Maven repository.

## How to Add Modules Back

### 1. Check Available Versions

Visit the OpenMRS Maven repository to find available versions:
- Base URL: `https://mavenrepo.openmrs.org/public/`
- For a specific module: `https://mavenrepo.openmrs.org/public/org/openmrs/module/{module-name}-omod/maven-metadata.xml`

Example for bedmanagement:
```bash
curl -s "https://mavenrepo.openmrs.org/public/org/openmrs/module/bedmanagement-omod/maven-metadata.xml" | grep "<version>"
```

### 2. Update pom.xml

1. Find the module in `openmrs/distro/pom.xml`
2. Uncomment the dependency
3. Update the version property at the top of the file to match an available version
4. Rebuild: `docker-compose build openmrs-backend`

### 3. Modules Currently Commented Out

These modules need version verification before uncommenting:

- **authentication-omod** - Check available versions
- **bedmanagement-omod** - Check available versions (you specifically need this)
- **registrationcore-omod** - Check available versions
- **registrationapp-omod** - Check available versions
- **coreapps-omod** - Check available versions
- **uiframework-omod** - Check available versions
- **uicommons-omod** - Check available versions
- And other Reference Application modules

### 4. Quick Test

After uncommenting a module, test the build:
```bash
docker-compose build openmrs-backend
```

If it fails, the version doesn't exist. Check the Maven repository for correct versions.

## Adding Custom Modules

To add your own custom modules:

1. Add the dependency to `openmrs/distro/pom.xml`:
```xml
<dependency>
    <groupId>com.yourcompany</groupId>
    <artifactId>your-module-omod</artifactId>
    <version>1.0.0</version>
</dependency>
```

2. If your module is in a private repository, add the repository to the `<repositories>` section.

3. Rebuild: `docker-compose build openmrs-backend`

## Essential Modules Currently Included

- initializer-omod (for initialization)
- fhir2-omod
- webservices.rest-omod
- idgen-omod
- legacyui-omod
- addresshierarchy-omod
- metadatamapping-omod
- openconceptlab-omod
- attachments-omod
- referencedemodata-omod
- queue-omod
- cohort-omod
- reporting-omod
- reportingrest-omod
- calculation-omod
- htmlwidgets-omod
- patientflags-omod
- o3forms-omod
- emrapi-omod
- event-omod

