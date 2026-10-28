ARG POLARIS_REPLICA_API_BASE_IMAGE
FROM ${POLARIS_REPLICA_API_BASE_IMAGE}
USER root
WORKDIR /app
COPY backend/app /app/app
COPY backend/skill_sources/aurora-ecommerce-reference-remix /app/skill_sources/aurora-ecommerce-reference-remix
COPY backend/skill_sources/all-category-style-benchmark-still /app/skill_sources/all-category-style-benchmark-still
COPY backend/source_manifests /app/source_manifests
COPY backend/migrations /app/migrations
COPY backend/alembic.ini /app/alembic.ini
USER 10001:10001
