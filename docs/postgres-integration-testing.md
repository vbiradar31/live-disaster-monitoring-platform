# Database configuration and integration tests

## Development database

`app.config.settings` is the shared resolver used by both the application and
Alembic. Host-side development launchers remove inherited test overrides in a
child PowerShell process, so the repository `.env` supplies `DATABASE_URL` and
the caller's environment remains unchanged.

From the repository root, inspect the sanitized target without connecting to
PostgreSQL:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./scripts/dev.ps1 -Operation Target
```

Run supported host-side Alembic commands through the development launcher. It
prints only host, port, and database name, then reads the current and pending
revisions in a read-only transaction before invoking Alembic:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./scripts/dev.ps1 -Operation Alembic -AlembicArguments current
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./scripts/dev.ps1 -Operation Migrate
```

The launcher rejects invalid targets and the integration-test database before
running Alembic. Start the host-side API with the same development settings:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./scripts/dev.ps1 -Operation Api
```

## PostgreSQL integration tests

Run the isolated PostgreSQL suite with:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./scripts/run-postgres-integration.ps1
```

The launcher starts the named test-only PostgreSQL Compose project when Docker
is available. If Docker management is unavailable, it may reuse an already
running service only after checking the guarded loopback endpoint and test
database identity. It sets test database variables only in a child PowerShell
process and never stops a service it did not start. Services it starts are
removed in a `finally` block. The caller's environment is not changed. Tests
create and drop only their generated `ldmp_it_*` databases on that isolated
service. The integration URL is guarded in the test fixture and cannot target
the development port or database.

For the regular SQLite/mock unit suite:

```powershell
./ingestion/.venv/Scripts/python.exe -m pytest -q tests --ignore=tests/postgres_integration
```

## One-time cleanup for an already contaminated PowerShell session

If this session contains the integration test's leftover variables, remove
them so Pydantic Settings falls back to the development `.env`:

```powershell
Remove-Item Env:DATABASE_URL, Env:POSTGRES_TEST_ADMIN_URL, Env:LDMP_DISABLE_DOTENV -ErrorAction SilentlyContinue
```

Use this cleanup only when `DATABASE_URL` is the stale integration override;
it removes the process environment values, not the `.env` file. The launchers
do not require this cleanup and do not print a connection URL or credentials.
