# syntax=docker/dockerfile:1.3
#--------------------------------------------
# Dev Stage - Assembles and Builds Frontend
#--------------------------------------------
FROM --platform=$BUILDPLATFORM node:22-alpine as dev

ARG APP_SHELL_VERSION=next

RUN mkdir -p /app
WORKDIR /app

COPY spa-assemble-config.json .
COPY spa-build-config.json .

RUN yarn global add husky

COPY packages/trigonal-esm-patient-report-app-1.0.0.tgz .
COPY packages/trigonal-esm-ipd-app-1.0.0.tgz .
COPY packages/kenyaemr-esm-imaging-orders-app-4.0.5.tgz .
COPY packages/kenyaemr-esm-procedure-orders-app-4.0.5.tgz .
COPY packages/kenyaemr-esm-medical-supply-orders-app-4.0.5.tgz .
COPY packages/kenyaemr-esm-bed-orders-app-4.0.5.tgz .
COPY packages/openmrs-esm-patient-orders-app-11.3.0.tgz .
COPY packages/trigonal-esm-patient-tests-app-1.0.0.tgz .
COPY packages/openmrs-esm-patient-orders-fast-app-12.0.1.tgz .
COPY packages/openmrs-esm-patient-chart-app-12.0.1.tgz .
COPY packages/openmrs-esm-orderset-app-12.0.1.tgz .
COPY packages/trigonal-esm-ward-app-1.0.0.tgz .

RUN yarn add ./trigonal-esm-patient-report-app-1.0.0.tgz
RUN yarn add ./trigonal-esm-ipd-app-1.0.0.tgz
RUN yarn add ./kenyaemr-esm-imaging-orders-app-4.0.5.tgz
RUN yarn add ./kenyaemr-esm-procedure-orders-app-4.0.5.tgz
RUN yarn add ./kenyaemr-esm-bed-orders-app-4.0.5.tgz
RUN yarn add ./openmrs-esm-patient-orders-app-11.3.0.tgz
RUN yarn add ./trigonal-esm-patient-tests-app-1.0.0.tgz
RUN yarn add ./openmrs-esm-patient-orders-fast-app-12.0.1.tgz
RUN yarn add ./openmrs-esm-patient-chart-app-12.0.1.tgz
RUN yarn add ./openmrs-esm-orderset-app-12.0.1.tgz
RUN yarn add ./trigonal-esm-ward-app-1.0.0.tgz 

ARG CACHE_BUST
RUN npx --legacy-peer-deps openmrs@${APP_SHELL_VERSION:-next} assemble --manifest --mode config --config spa-assemble-config.json --target ./spa
RUN npx --legacy-peer-deps openmrs@${APP_SHELL_VERSION:-next} build --build-config spa-build-config.json --target ./spa
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

CMD ["/usr/local/bin/startup.sh"]
