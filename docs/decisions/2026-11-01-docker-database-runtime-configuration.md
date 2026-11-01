# Docker and database runtime configuration

- **Date:** 2026-11-01
- **Status:** Accepted

## Context

The ingestion Dockerfile used a multi-line JSON-form `CMD` that was not valid
exec-form syntax. Compose assembled `DATABASE_URL` by interpolating raw
credentials, which is fragile when credential values contain URL-reserved
characters. The image also included Alembic and migration files even though
migrations are run by the host development workflow, outside application
startup.

The application already supports `DATABASE_URL` from the repository `.env` for
host development. Container configuration must be supplied at runtime; local
`.env` files and credentials should not be copied into the image.

## Decision

- Use a valid single-line exec-form Uvicorn command.
- Keep the legacy `DATABASE_URL` setting for local development and migration
  tooling. For Compose, pass PostgreSQL fields separately and construct the URL
  with SQLAlchemy `URL.create()`.
- Keep the image limited to runtime dependencies and application code. Keep
  Alembic in development dependencies because migration commands run externally
  to application startup.
- Keep the existing PostgreSQL and MongoDB service definitions, persistent
  volumes, ports, and health-check behavior unchanged.

## Alternatives considered

- Continue composing a complete URL in Compose: rejected because raw credential
  interpolation can confuse URL parsing when values contain reserved
  characters.
- Copy `.env` into the image: rejected because runtime configuration is injected
  and local settings or secrets do not belong in the image.
- Keep migration files and Alembic in the runtime image: rejected because the
  migration workflow is external to application startup.

## Rationale and trade-offs

`URL.create()` receives credentials as separate values and lets SQLAlchemy
handle URL encoding. Keeping `DATABASE_URL` preserves the established host
development and migration path, while Compose uses the structured settings.
The runtime image is smaller and contains no migration tooling, so migration
operations must continue to use the development environment.

## Affected files

- `docker-compose.yml`
- `ingestion/Dockerfile`
- `ingestion/app/config.py`
- `ingestion/app/database.py`
- `ingestion/requirements.txt`
- `ingestion/requirements-dev.txt`
- `tests/test_ingestion_config.py`

## Verification

For this implementation, the Docker image build completed successfully. The
Compose ingestion service started healthy, and `GET /health` returned a healthy
status with PostgreSQL connected. Image inspection confirmed `/app/.env`,
Alembic files, and the Alembic package were absent. The unit suite reported
42 passed; the isolated PostgreSQL integration workflow reported 8 passed. The
integration run emitted a pytest cache permission warning but completed
successfully. `git diff --check` completed without errors.
