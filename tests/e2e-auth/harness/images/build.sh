#!/usr/bin/env bash
# Build the full-harness images and helper libraries from THIS working tree (tests/e2e-auth/harness/sso-openmrs.yml).
#
#   harness/images/build.sh [openmrs] [frontend] [openelis] [openelis-frontend] [cis] [dhis2] [libs]   no argument = all
#
# Images: nidan-openmrs-harness, nidan-openmrs-frontend-harness, nidan-openelis-harness (WAR from ../OpenELIS-Global-2),
# nidan-openelis-frontend-harness (../OpenELIS-Global-2/frontend, its Dockerfile.prod: the login page as this tree has it),
# nidan-cis-harness (jar from ../nidan-middleware), nidan-dhis2-harness (../nidan-dhis2-integration).
# libs: OCA auth_oidc (server-auth 19.0) and the Python libraries the harness Odoo/Superset load, into harness/.build.
# Print the matching env with:  harness/images/build.sh env
# Build contexts live in harness/.build (git-ignored); nothing outside it is written. Needs docker, mvn, git, pip.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
nd=$(cd "$here/../../../.." && pwd)            # nidan-docker/
repo=$(cd "$nd/.." && pwd)                     # nidanhis/ (sibling repos)
mkdir -p "$here/../.build"
out=$(cd "$here/../.build" && pwd)
want=("$@"); [ ${#want[@]} -eq 0 ] && want=(openmrs frontend openelis openelis-frontend cis dhis2 libs)

ctx() { local d="$out/ctx-$1"; [ -d "$d" ] || mkdir -p "$d"; echo "$d"; }

for w in "${want[@]}"; do
  case "$w" in
    env)
      echo "export OCA_AUTH_OIDC=$out/server-auth/auth_oidc ODOO_PYLIBS=$out/pylibs-odoo SUPERSET_PYLIBS=$out/pylibs-superset"
      echo "export OPENMRS_CUSTOM_MODULES=\${OPENMRS_CUSTOM_MODULES:-$nd/openmrs/custom_modules}"
      exit 0 ;;
    openmrs)
      # the oauth2login OMOD is a build output, kept out of git like custom_modules (startup-custom.sh needs it)
      ls "$nd"/openmrs/sso_modules/oauth2login-*.omod >/dev/null 2>&1 || {
        echo "no oauth2login OMOD in nidan-docker/openmrs/sso_modules: build openmrs-module-oauth2login and copy" \
          "omod/target/oauth2login-*.omod there" >&2; exit 1; }
      d=$(ctx openmrs)
      cp "$nd/openmrs/startup-custom.sh" "$d/"
      rsync -a --delete "$nd/openmrs/sso/" "$d/sso/"
      rsync -a --delete "$nd/openmrs/sso_modules/" "$d/sso_modules/"
      docker build -q -f "$here/openmrs.Dockerfile" -t nidan-openmrs-harness "$d" ;;
    frontend)
      d=$(ctx frontend)
      t="$out/esm-login-tgz"; mkdir -p "$t"
      tar xzf "$nd"/openmrs/frontend/packages/openmrs-esm-login-app-10.0.0-nidan.2.tgz -C "$t"
      rsync -a --delete "$t/package/dist/" "$d/login-dist/"
      cp "$nd/openmrs/frontend/config-sso.json" "$nd/openmrs/frontend/startup.sh" "$d/"
      docker build -q -f "$here/frontend.Dockerfile" -t nidan-openmrs-frontend-harness "$d" ;;
    openelis)
      (cd "$repo/OpenELIS-Global-2" && mvn -q package -DskipTests -Dmaven.test.skip=true)
      d=$(ctx openelis)
      cp "$repo/OpenELIS-Global-2/target/OpenELIS-Global.war" "$d/"
      docker build -q --platform linux/amd64 -f "$here/openelis.Dockerfile" -t nidan-openelis-harness "$d" ;;
    openelis-frontend)
      d=$(ctx openelis-frontend)     # the source only: a local node_modules or build must not reach the image
      rsync -a --delete --exclude node_modules --exclude build --exclude cypress --exclude playwright \
        "$repo/OpenELIS-Global-2/frontend/" "$d/"
      docker build -q --platform linux/amd64 -f "$d/Dockerfile.prod" -t nidan-openelis-frontend-harness "$d" ;;
    cis)
      (cd "$repo/nidan-middleware/integration" && mvn -q -pl cis -am package -DskipTests)
      d=$(ctx cis)
      cp "$repo/nidan-middleware/integration/cis/target/nidan-cis-0.1.0-SNAPSHOT.jar" "$d/app.jar"
      docker build -q -f "$here/cis.Dockerfile" -t nidan-cis-harness "$d" ;;
    dhis2)
      docker build -q -t nidan-dhis2-harness "$repo/nidan-dhis2-integration" ;;
    libs)
      [ -d "$out/server-auth/.git" ] || git clone -q --depth 1 -b 19.0 https://github.com/OCA/server-auth.git "$out/server-auth"
      python3 -m pip install -q --target "$out/pylibs-odoo" --upgrade 'python-jose==3.5.0'
      python3 -m pip install -q --target "$out/pylibs-superset" --upgrade --no-deps 'Authlib==1.3.2' ;;
    *) echo "unknown image: $w" >&2; exit 2 ;;
  esac
  echo "built: $w"
done
