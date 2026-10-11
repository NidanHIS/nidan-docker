# Harness only: the published SPA with the patched esm-login bundle (KC-35), config-sso.json and this repo's startup.sh.
FROM trigonaltechnology/openmrs-frontend:latest
COPY login-dist/ /tmp/login-dist/
RUN d=/usr/share/nginx/html/openmrs-esm-login-app-10.0.0-nidan && test -d $d && rm -rf $d && mv /tmp/login-dist $d
COPY config-sso.json /usr/share/nginx/html/
COPY --chmod=755 startup.sh /usr/local/bin/startup.sh
