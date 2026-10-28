ARG POLARIS_PYTHON_BASE_IMAGE
FROM ${POLARIS_PYTHON_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_REQUIRE_HASHES=1

WORKDIR /app

# Runtime dependencies are fully pinned and hash-locked by the product commit.
COPY backend/requirements.lock ./requirements.lock
RUN python -m pip install --require-hashes --only-binary=:all: -r requirements.lock

COPY backend/alembic.ini ./
COPY backend/app ./app
COPY backend/catalog_assets ./catalog_assets
COPY backend/skill_sources ./skill_sources
COPY backend/source_manifests ./source_manifests
COPY backend/migrations ./migrations
COPY backend/scripts/assign_workspace_owner.py ./scripts/assign_workspace_owner.py

RUN groupadd --system --gid 10001 polaris \
    && useradd --system --uid 10001 --gid polaris --home-dir /app polaris \
    && mkdir -p /app/.data/aurora-main-image \
    && chown -R polaris:polaris /app

USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
