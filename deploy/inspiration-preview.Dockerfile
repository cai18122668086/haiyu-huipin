ARG POLARIS_MAIN_API_BASE_IMAGE
FROM ${POLARIS_MAIN_API_BASE_IMAGE}
USER root
WORKDIR /app
COPY backend/app/inspiration_routes.py /app/app/inspiration_routes.py
USER 10001:10001
