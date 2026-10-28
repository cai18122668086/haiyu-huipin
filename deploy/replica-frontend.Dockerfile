ARG POLARIS_REPLICA_FRONTEND_BASE_IMAGE
FROM ${POLARIS_REPLICA_FRONTEND_BASE_IMAGE}
USER root
WORKDIR /app
ENV VITE_LOCAL_DEV_AUTH=false
COPY app /app/app
COPY components /app/components
COPY lib /app/lib
COPY features /app/features
RUN cp -a /app/dist/client/_polaris-static /tmp/polaris-previous-static \
    && npm run build \
    && cp -an /tmp/polaris-previous-static/. /app/dist/client/_polaris-static/ \
    && rm -r /tmp/polaris-previous-static
USER 1000:1000
