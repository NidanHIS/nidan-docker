# OpenELIS Keycloak Realm Import

Place the OpenELIS realm export JSON here so Keycloak can import it on startup.

Steps:
1. Obtain the realm export from the OpenELIS SSO repo (e.g., `openelis-sso-keycloak`) or your existing Keycloak instance.
2. Copy the JSON file into this directory (e.g., `openelis-realm.json`).
3. Start Keycloak (already configured with `--import-realm`). Files in this folder are mounted to `/opt/keycloak/data/import/openelis` inside the container.

If you need a sample, see the OpenELIS SSO repo:
- https://github.com/DIGI-UW/openelis-sso-keycloak

