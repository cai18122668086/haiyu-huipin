FROM python:3.13.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md alembic.ini ./
COPY requirements-source-pixels.lock ./requirements-source-pixels.lock
COPY app ./app
COPY skill_sources ./skill_sources
COPY source_manifests ./source_manifests
COPY catalog_assets ./catalog_assets
COPY migrations ./migrations
COPY scripts/fetch_source_matte_model.py ./scripts/fetch_source_matte_model.py
COPY scripts/check_source_pixel_runtime.py ./scripts/check_source_pixel_runtime.py
COPY third_party ./third_party

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --require-hashes -r requirements-source-pixels.lock \
    && python -m pip install .

RUN PYTHONPATH=/app python scripts/fetch_source_matte_model.py
RUN PYTHONPATH=/app python scripts/check_source_pixel_runtime.py

RUN mkdir -p /app/.data/aurora-main-image \
    && groupadd --system --gid 10001 polaris \
    && useradd --system --uid 10001 --gid polaris --home-dir /app polaris \
    && chown -R polaris:polaris /app

USER polaris

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
