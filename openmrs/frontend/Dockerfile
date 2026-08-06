# syntax=docker/dockerfile:1.3
#--------------------------------------------
# Dev Stage - Nidan default: Nepali (BS) calendar enabled
# For vanilla upstream OpenMRS date pickers use Dockerfile.vanilla
#--------------------------------------------
FROM --platform=$BUILDPLATFORM node:22-alpine as dev

ARG APP_SHELL_VERSION=next
# Bump after repacking nidan-esm-nepali-calendar-1.0.0.tgz to bust docker layer cache.
ARG NIDAN_CALENDAR_BUNDLE=1.0.0-v9

RUN mkdir -p /app
WORKDIR /app

COPY spa-assemble-config.json spa-build-config.json .

RUN yarn global add husky

# Third-party / fork tarballs (paths must match spa-assemble-config.json)
COPY packages/trigonal-esm-patient-report-app-1.0.0.tgz .
COPY packages/trigonal-esm-ipd-app-1.0.0.tgz .
COPY packages/trigonal-esm-patient-tests-app-1.0.0.tgz .
COPY packages/trigonal-esm-ward-app-1.0.0.tgz .
COPY packages/kenyaemr-esm-imaging-orders-app-4.0.5.tgz .
COPY packages/kenyaemr-esm-procedure-orders-app-4.0.5.tgz .
COPY packages/kenyaemr-esm-medical-supply-orders-app-4.0.5.tgz .
COPY packages/kenyaemr-esm-bed-orders-app-4.0.5.tgz .
COPY packages/openmrs-esm-nidan-core-app-1.0.0.tgz .
COPY packages/nidan-esm-nepali-calendar-1.0.0.tgz .
COPY packages/openmrs-esm-form-builder-app-3.4.1-trigonal.tgz .
COPY packages/openmrs-esm-form-engine-app-12.1.0-trigonal.tgz .
COPY packages/openmrs-esm-service-queues-app-10.0.2.tgz .
COPY packages/openmrs-esm-paywall-app-1.0.0.tgz .
COPY packages/openmrs-esm-login-app-10.0.0-nidan.tgz .

# Nidan patient-chart fork (@openmrs/esm-patient-chart 12.1.0 + extensions)
COPY packages/openmrs-esm-patient-chart-app-12.1.0.tgz .
COPY packages/openmrs-esm-patient-forms-app-12.1.0.tgz .
COPY packages/openmrs-esm-patient-orders-app-12.1.0.tgz .
COPY packages/openmrs-esm-orderset-app-12.0.1.tgz .
COPY packages/openmrs-esm-patient-orders-fast-app-12.0.1.tgz .
COPY packages/openmrs-esm-patient-medications-app-12.1.0-trigonal.tgz .
COPY packages/openmrs-esm-patient-notes-app-12.1.0.tgz .

RUN yarn add ./trigonal-esm-patient-report-app-1.0.0.tgz
RUN yarn add ./trigonal-esm-ipd-app-1.0.0.tgz
RUN yarn add ./trigonal-esm-patient-tests-app-1.0.0.tgz
RUN yarn add ./trigonal-esm-ward-app-1.0.0.tgz
RUN yarn add ./kenyaemr-esm-imaging-orders-app-4.0.5.tgz
RUN yarn add ./kenyaemr-esm-procedure-orders-app-4.0.5.tgz
RUN yarn add ./kenyaemr-esm-medical-supply-orders-app-4.0.5.tgz
RUN yarn add ./kenyaemr-esm-bed-orders-app-4.0.5.tgz
RUN yarn add ./openmrs-esm-nidan-core-app-1.0.0.tgz
RUN yarn add ./openmrs-esm-paywall-app-1.0.0.tgz
RUN yarn add ./openmrs-esm-patient-notes-app-12.1.0.tgz
RUN yarn add ./openmrs-esm-login-app-10.0.0-nidan.tgz

RUN echo "nidan-calendar-bundle=${NIDAN_CALENDAR_BUNDLE}" && \
    yarn add ./nidan-esm-nepali-calendar-1.0.0.tgz

# openmrs CLI must live in /app/node_modules (not npx cache) so patches apply to the same tree `openmrs build` uses.
RUN yarn add openmrs@${APP_SHELL_VERSION:-next} --legacy-peer-deps
RUN yarn add ./openmrs-esm-form-builder-app-3.4.1-trigonal.tgz
RUN yarn add ./openmrs-esm-form-engine-app-12.1.0-trigonal.tgz
RUN yarn add ./openmrs-esm-service-queues-app-10.0.2.tgz
RUN yarn add ./openmrs-esm-patient-chart-app-12.1.0.tgz
RUN yarn add ./openmrs-esm-patient-forms-app-12.1.0.tgz
RUN yarn add ./openmrs-esm-patient-orders-app-12.1.0.tgz
RUN yarn add ./openmrs-esm-orderset-app-12.0.1.tgz
RUN yarn add ./openmrs-esm-patient-orders-fast-app-12.0.1.tgz
RUN yarn add ./openmrs-esm-patient-medications-app-12.1.0-trigonal.tgz

RUN yarn openmrs assemble --manifest --mode config --config spa-assemble-config.json --target ./spa

RUN node node_modules/@nidan/esm-nepali-calendar/tools/spa-build-hook.cjs patch

RUN yarn openmrs build --build-config spa-build-config.json --target ./spa

RUN node node_modules/@nidan/esm-nepali-calendar/tools/spa-build-hook.cjs verify ./spa

RUN if [ ! -f ./spa/index.html ]; then echo 'Build failed. Please check the logs above for details. This may have happened because of an update to a library that OpenMRS depends on.'; exit 1; fi

#--------------------------------------------
# Runtime Stage - Published Image
#--------------------------------------------
FROM nginx:1.25-alpine

RUN apk update && \
    apk upgrade && \
    # add more utils for sponge and envsubst
    apk add --no-cache moreutils

# clear any default files installed by nginx
RUN rm -rf /usr/share/nginx/html/*

COPY startup.sh /usr/local/bin/startup.sh
RUN chmod +x /usr/local/bin/startup.sh

# Copy nginx config for SPA routing
COPY nginx.conf /etc/nginx/nginx.conf

COPY --from=dev /app/spa /usr/share/nginx/html
COPY config-core_demo.json /usr/share/nginx/html

COPY /resources/favicon.ico /usr/share/nginx/html/favicon.ico
COPY /resources/nidan-logo-full.png /usr/share/nginx/html

CMD ["/usr/local/bin/startup.sh"]
