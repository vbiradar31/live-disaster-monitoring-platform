"""Opt-in integration tests for a disposable local PostgreSQL container.

Run with docker-compose.integration.yml and POSTGRES_TEST_ADMIN_URL set to the
test-only postgres database. Each test creates uniquely named databases and
drops only those databases after verifying their generated names.
"""
from __future__ import annotations

import asyncio
import os
import json
import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
import httpx
import pytest
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[2]
ADMIN_URL_VALUE = os.environ.get("POSTGRES_TEST_ADMIN_URL")
if ADMIN_URL_VALUE:
    # The explicit test-only admin URL is also the app's harmless default
    # connection setting; all database sessions in these tests use a generated
    # database name. Disable .env loading before importing application modules.
    os.environ["DATABASE_URL"] = ADMIN_URL_VALUE
    os.environ["LDMP_DISABLE_DOTENV"] = "1"
pytestmark = pytest.mark.postgres


def _safe_admin_url() -> URL:
    if not ADMIN_URL_VALUE:
        pytest.skip("POSTGRES_TEST_ADMIN_URL is unset; PostgreSQL integration tests were not run")
    url = make_url(ADMIN_URL_VALUE)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"127.0.0.1", "localhost"}
        or url.port != 55432
        or url.database != "postgres"
        or url.username != "ldmp_test"
    ):
        pytest.fail("Refusing PostgreSQL tests: URL must target the isolated ldmp_test service on localhost:55432/postgres")
    return url


@pytest.fixture
def database_factory():
    admin_url = _safe_admin_url()
    engine = create_engine(admin_url, pool_pre_ping=True)
    created: list[str] = []

    def create_database(label: str) -> URL:
        name = f"ldmp_it_{label}_{uuid.uuid4().hex[:12]}"
        assert name.startswith("ldmp_it_")
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        created.append(name)
        return admin_url.set(database=name)

    yield create_database

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        for name in created:
            if not name.startswith("ldmp_it_"):
                pytest.fail("Refusing to drop a database not created by this test")
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    engine.dispose()


def _alembic(database_url: URL, revision: str) -> str:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url.render_as_string(hide_password=False)
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout + completed.stderr


def _final_revision(database_url: URL) -> str | None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    finally:
        engine.dispose()


def test_fresh_migrations_match_expected_postgres_schema(database_factory):
    database_url = database_factory("fresh")
    _alembic(database_url, "head")
    assert _final_revision(database_url) == "b4a2d91f63e8"

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {
            "sources", "ingestion_runs", "events", "source_observations",
            "earthquake_observations", "event_geometries", "event_episodes",
            "episode_observations", "event_correlations", "event_correlation_candidates",
        } <= tables
        assert "source_providers" not in tables

        observations = {column["name"]: column for column in inspector.get_columns("source_observations")}
        assert observations["raw_payload"]["nullable"] is True
        assert observations["event_id"]["nullable"] is True
        unique_sets = [set(item["column_names"]) for item in inspector.get_unique_constraints("source_observations")]
        assert {"source_id", "provider_record_id"} in unique_sets
        event_indexes = {item["name"] for item in inspector.get_indexes("events")}
        assert "ix_events_legacy_identity" in event_indexes
        assert {fk["referred_table"] for fk in inspector.get_foreign_keys("episode_observations")} == {
            "event_episodes", "source_observations"
        }

        # Alembic's metadata comparison checks mapped table/column/index/constraint drift.
        env = os.environ.copy()
        env["DATABASE_URL"] = database_url.render_as_string(hide_password=False)
        compared = subprocess.run(
            [sys.executable, "-m", "alembic", "check"], cwd=ROOT, env=env,
            capture_output=True, text=True, check=False,
        )
        assert compared.returncode == 0, compared.stdout + compared.stderr
    finally:
        engine.dispose()


def test_legacy_rows_survive_backfill_and_current_service_can_read_them(database_factory):
    database_url = database_factory("legacy")
    _alembic(database_url, "36eab4f3c6a6")
    engine = create_engine(database_url)
    event_id = uuid.uuid4()
    incomplete_event_id = uuid.uuid4()
    episode_id = uuid.uuid4()
    geometry_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO events (id, external_id, source, title, event_type, status, created_at, updated_at)
            VALUES (:id, 'us7000legacy', 'USGS', 'Legacy quake', 'earthquake', 'active', now(), now())
        """), {"id": event_id})
        connection.execute(text("""
            INSERT INTO events (id, external_id, source, title, event_type, status, created_at, updated_at)
            VALUES (:id, 'unknown-legacy', 'unknown', 'Legacy event without evidence', 'earthquake', 'active', now(), now())
        """), {"id": incomplete_event_id})
        connection.execute(text("""
            INSERT INTO event_episodes (id, event_id, episode_number, title, started_at)
            VALUES (:id, :event_id, 1, 'Initial episode', now())
        """), {"id": episode_id, "event_id": event_id})
        connection.execute(text("""
            INSERT INTO event_geometries (id, event_id, geometry_type, geometry_json, observed_at)
            VALUES (:id, :event_id, 'Point', '{"type":"Point","coordinates":[20,10,12]}'::jsonb, now())
        """), {"id": geometry_id, "event_id": event_id})
    engine.dispose()

    _alembic(database_url, "9f3b7e21a6c4")
    source_id, run_id, observation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO source_providers (id, code, name, enabled, created_at) VALUES (:id, 'usgs', 'USGS', true, now())"), {"id": source_id})
        connection.execute(text("""
            INSERT INTO ingestion_runs (id, provider_id, started_at, status, records_received, records_inserted, records_duplicate)
            VALUES (:id, :provider_id, now(), 'completed', 1, 1, 0)
        """), {"id": run_id, "provider_id": source_id})
        connection.execute(text("""
            INSERT INTO source_observations
                (id, provider_id, provider_event_id, ingestion_run_id, event_id, origin_time,
                 latitude, longitude, magnitude, depth_km, title, raw_payload, match_status, created_at)
            VALUES (:id, :provider_id, 'us7000legacy', :run_id, :event_id, '2026-01-01T00:00:00Z',
                    10, 20, 5.4, 12, 'Legacy quake', '{"properties":{"status":"reviewed"}}', 'linked', now())
        """), {"id": observation_id, "provider_id": source_id, "run_id": run_id, "event_id": event_id})
    engine.dispose()

    _alembic(database_url, "head")
    assert _final_revision(database_url) == "b4a2d91f63e8"
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT legacy_external_id FROM events WHERE id=:id"), {"id": event_id}).scalar_one() == "us7000legacy"
            assert connection.execute(text("SELECT legacy_external_id FROM events WHERE id=:id"), {"id": incomplete_event_id}).scalar_one() == "unknown-legacy"
            detail = connection.execute(text("SELECT origin_time, latitude, longitude, magnitude, depth_km, provider_status FROM earthquake_observations WHERE observation_id=:id"), {"id": observation_id}).one()
            assert (detail.latitude, detail.longitude, detail.magnitude, detail.depth_km, detail.provider_status) == (10, 20, 5.4, 12, "reviewed")
            assert connection.execute(text("SELECT count(*) FROM event_episodes WHERE id=:id AND event_id=:event_id"), {"id": episode_id, "event_id": event_id}).scalar_one() == 1
            assert connection.execute(text("SELECT count(*) FROM event_geometries WHERE id=:id AND event_id=:event_id"), {"id": geometry_id, "event_id": event_id}).scalar_one() == 1
            assert connection.execute(text("SELECT provider_event_id FROM source_observations WHERE id=:id"), {"id": observation_id}).scalar_one() == "us7000legacy"
    finally:
        engine.dispose()


def test_postgres_concurrent_revisions_serialize_previous_values(database_factory):
    database_url = database_factory("concurrency")
    _alembic(database_url, "head")
    # Imports follow the test-only DATABASE_URL setup; all sessions below use the
    # explicitly created disposable database URL, never the application's URL.
    from app.models import EarthquakeObservation, IngestionRun, Source, SourceObservation
    from app.schemas.earthquake import NormalizedEarthquake
    from app.services.earthquake_ingestion import EarthquakeIngestionService

    engine = create_engine(database_url, pool_size=5)
    with Session(engine) as setup:
        source = Source(code="usgs", name="USGS")
        setup.add(source)
        setup.flush()
        runs = [IngestionRun(source_id=source.id, status="running") for _ in range(3)]
        setup.add_all(runs)
        setup.commit()
        source_id, initial_run, run_a, run_b = source.id, runs[0].id, runs[1].id, runs[2].id

        def observation(magnitude: float) -> NormalizedEarthquake:
            return NormalizedEarthquake(
                provider="usgs", provider_record_id="us7000concurrent", provider_event_id="us7000concurrent",
                origin_time=datetime(2026, 1, 1, tzinfo=UTC), latitude=10, longitude=20,
                magnitude=magnitude, raw_payload={"mag": magnitude},
            )

        service = EarthquakeIngestionService(setup, providers=[])
        service._persist_observation(source_id, initial_run, observation(5.4))
        setup.commit()

    a_has_lock = threading.Event()
    release_a = threading.Event()
    b_attempting_lock = threading.Event()
    errors: list[BaseException] = []

    def worker_a():
        try:
            with Session(engine) as session:
                EarthquakeIngestionService(session, providers=[])._persist_observation(source_id, run_a, observation(5.5))
                a_has_lock.set()
                if not release_a.wait(10):
                    raise TimeoutError("test synchronization timed out")
                session.commit()
        except BaseException as exc:
            errors.append(exc)
            release_a.set()

    def worker_b():
        try:
            with Session(engine) as session:
                from sqlalchemy import event

                @event.listens_for(engine, "before_cursor_execute")
                def detect_lock(_conn, _cursor, statement, _params, _context, _many):
                    if "FOR UPDATE" in statement.upper():
                        b_attempting_lock.set()

                EarthquakeIngestionService(session, providers=[])._persist_observation(source_id, run_b, observation(5.8))
                session.commit()
        except BaseException as exc:
            errors.append(exc)

    thread_a = threading.Thread(target=worker_a)
    thread_a.start()
    assert a_has_lock.wait(10), "first transaction did not reach the held revision update"
    thread_b = threading.Thread(target=worker_b)
    thread_b.start()
    assert b_attempting_lock.wait(10), "second transaction did not attempt SELECT FOR UPDATE"
    release_a.set()
    thread_a.join(10)
    thread_b.join(10)
    assert not thread_a.is_alive() and not thread_b.is_alive()
    assert not errors, repr(errors)

    with Session(engine) as check:
        observations = check.scalars(select(SourceObservation)).all()
        assert len(observations) == 1
        detail = check.get(EarthquakeObservation, observations[0].id)
        assert (detail.magnitude, detail.previous_magnitude) == (5.8, 5.5)
    engine.dispose()


def test_postgres_revision_null_policy_and_raw_payload_retention(database_factory):
    database_url = database_factory("retention")
    _alembic(database_url, "head")
    from app.models import EarthquakeObservation, SourceObservation
    from app.schemas.earthquake import NormalizedEarthquake
    from app.services.earthquake_ingestion import EarthquakeIngestionService

    class Provider:
        name = "usgs"
        records = []

        def fetch(self):
            return self.records

        def close(self):
            pass

    provider = Provider()

    def item(magnitude=5.4, latitude=10.0, longitude=20.0, status="reviewed", payload=None):
        values = {
            "provider": "usgs", "provider_record_id": "us7000revision", "provider_event_id": "us7000revision",
            "origin_time": datetime(2026, 1, 1, tzinfo=UTC), "latitude": latitude,
            "longitude": longitude, "raw_payload": payload or {"version": magnitude},
        }
        if magnitude is not ...:
            values["magnitude"] = magnitude
        if status is not ...:
            values["provider_status"] = status
        return NormalizedEarthquake(**values)

    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            service = EarthquakeIngestionService(session, [provider])
            provider.records = [item()]
            service.run_all()
            observation = session.scalar(select(SourceObservation))
            detail = session.get(EarthquakeObservation, observation.id)
            assert detail.magnitude == 5.4 and detail.previous_magnitude is None
            original_expiry = observation.raw_payload_expires_at

            provider.records = [item(magnitude=5.5, payload={"version": 2})]
            service.run_all()
            assert (detail.magnitude, detail.previous_magnitude) == (5.5, 5.4)
            assert observation.raw_payload_expires_at > original_expiry

            provider.records = [item(magnitude=5.8, latitude=10.1, longitude=20.1, payload={"version": 3})]
            service.run_all()
            assert (detail.magnitude, detail.previous_magnitude) == (5.8, 5.5)
            assert (detail.latitude, detail.longitude) == (10.1, 20.1)
            assert (detail.previous_latitude, detail.previous_longitude) == (10.0, 20.0)

            # Missing and explicit null follow the current policy: preserve the
            # last valid normalized value, including status and coordinate pair.
            provider.records = [item(magnitude=..., status=..., latitude=None, longitude=None, payload={"version": 4})]
            service.run_all()
            assert (detail.magnitude, detail.provider_status) == (5.8, "reviewed")
            assert (detail.latitude, detail.longitude) == (10.1, 20.1)
            provider.records = [item(magnitude=None, status=None, latitude=10.2, longitude=20.2, payload={"version": 5})]
            service.run_all()
            assert (detail.magnitude, detail.provider_status) == (5.8, "reviewed")
            assert (detail.latitude, detail.longitude) == (10.2, 20.2)
            assert (detail.previous_latitude, detail.previous_longitude) == (10.1, 20.1)

            previous = (detail.magnitude, detail.previous_magnitude, detail.latitude, detail.longitude)
            provider.records = [item(magnitude=5.8, latitude=10.2, longitude=20.2, payload={"version": 6})]
            service.run_all()
            assert (detail.magnitude, detail.previous_magnitude, detail.latitude, detail.longitude) == previous

            observation.raw_payload_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
            provider.records = []
            service.run_all()
            session.refresh(observation)
            assert observation.raw_payload is None and observation.raw_payload_expires_at is None
            assert session.get(EarthquakeObservation, observation.id).magnitude == 5.8
            assert session.scalar(select(SourceObservation).where(SourceObservation.provider_record_id == "us7000revision")) is not None
    finally:
        engine.dispose()


def test_provider_http_to_postgres_is_idempotent_and_tracks_revisions(database_factory):
    database_url = database_factory("pipeline")
    _alembic(database_url, "head")
    from app.models import EarthquakeObservation, Event, EventCorrelation, Source, SourceObservation
    from app.providers.gdacs import GDACSProvider
    from app.providers.usgs import USGSProvider
    from app.services.earthquake_ingestion import EarthquakeIngestionService

    fixtures = ROOT / "tests" / "fixtures"
    bodies = {
        "gdacs": json.loads((fixtures / "gdacs_earthquake.json").read_text(encoding="utf-8")),
        "usgs": json.loads((fixtures / "usgs_earthquake.json").read_text(encoding="utf-8")),
    }
    bodies["usgs"]["features"][0]["properties"]["status"] = "reviewed"

    def handler(request: httpx.Request) -> httpx.Response:
        if "gdacs.org" in request.url.host:
            return httpx.Response(200, json=bodies["gdacs"])
        if "earthquake.usgs.gov" in request.url.host:
            return httpx.Response(200, json=bodies["usgs"])
        return httpx.Response(404)

    engine = create_engine(database_url)
    try:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, Session(engine) as session:
            service = EarthquakeIngestionService(
                session, providers=[GDACSProvider(client=client), USGSProvider(client=client)]
            )
            first = service.run_all()
            assert [result["status"] for result in first["providers"]] == ["completed", "completed"]
            rows = session.scalars(select(SourceObservation).order_by(SourceObservation.provider_record_id)).all()
            assert len(rows) == 2
            assert rows[0].provider_record_id == "1476137:1632460"
            assert rows[1].provider_record_id == "us7000test"
            assert rows[0].event_id == rows[1].event_id
            assert session.scalar(select(func.count()).select_from(Event)) == 1
            usgs_row = next(row for row in rows if row.provider_record_id == "us7000test")
            stable_observation_id, stable_event_id = usgs_row.id, usgs_row.event_id
            detail = session.get(EarthquakeObservation, stable_observation_id)
            assert (detail.magnitude, detail.depth_km, detail.provider_status) == (6.8, 24.0, "reviewed")
            assert (detail.latitude, detail.longitude) == (38.322, 142.369)
            assert usgs_row.raw_payload["id"] == "us7000test"

            repeated = service.run_all()
            assert [result["unchanged"] for result in repeated["providers"]] == [1, 1]
            assert session.scalar(select(func.count()).select_from(SourceObservation)) == 2
            assert session.scalar(select(func.count()).select_from(Event)) == 1
            assert session.scalar(select(func.count()).select_from(EventCorrelation)) == 2

            props = bodies["usgs"]["features"][0]["properties"]
            geometry = bodies["usgs"]["features"][0]["geometry"]["coordinates"]
            props.update({"mag": 6.9, "status": "automatic", "updated": props["updated"] + 1000})
            geometry[:] = [142.370, 38.323, 25.0]
            service.run_all()
            assert session.scalar(select(func.count()).select_from(SourceObservation)) == 2
            assert session.scalar(select(func.count()).select_from(Event)) == 1
            session.refresh(usgs_row)
            session.refresh(detail)
            assert usgs_row.id == stable_observation_id and usgs_row.event_id == stable_event_id
            assert (detail.magnitude, detail.previous_magnitude) == (6.9, 6.8)
            assert (detail.depth_km, detail.previous_depth_km) == (25.0, 24.0)
            assert (detail.latitude, detail.longitude) == (38.323, 142.370)
            assert (detail.previous_latitude, detail.previous_longitude) == (38.322, 142.369)
            assert (detail.provider_status, detail.previous_provider_status) == ("automatic", "reviewed")

            props.update({"mag": 7.0, "status": "reviewed", "updated": props["updated"] + 1000})
            geometry[:] = [142.371, 38.324, 26.0]
            service.run_all()
            session.refresh(detail)
            assert (detail.magnitude, detail.previous_magnitude) == (7.0, 6.9)
            assert (detail.depth_km, detail.previous_depth_km) == (26.0, 25.0)
            assert (detail.latitude, detail.longitude) == (38.324, 142.371)
            assert (detail.previous_latitude, detail.previous_longitude) == (38.323, 142.370)
            assert (detail.provider_status, detail.previous_provider_status) == ("reviewed", "automatic")
            assert session.scalar(select(func.count()).select_from(Event)) == 1
    finally:
        engine.dispose()


def test_provider_http_failure_isolated_and_ambiguous_candidates_stay_unresolved(database_factory):
    database_url = database_factory("correlation")
    _alembic(database_url, "head")
    from app.models import Event, EventCorrelation, EventCorrelationCandidate, EventGeometry, SourceObservation
    from app.providers.gdacs import GDACSProvider
    from app.providers.usgs import USGSProvider
    from app.services.earthquake_ingestion import EarthquakeIngestionService

    fixtures = ROOT / "tests" / "fixtures"
    gdacs_body = json.loads((fixtures / "gdacs_earthquake.json").read_text(encoding="utf-8"))
    usgs_body = json.loads((fixtures / "usgs_earthquake.json").read_text(encoding="utf-8"))
    engine = create_engine(database_url)
    try:
        # Two legacy canonical geometries are both plausible; neither should be
        # automatically merged. The same run also exercises independent HTTP failure.
        with Session(engine) as session:
            event_ids = [uuid.uuid4(), uuid.uuid4()]
            for event_id, longitude in zip(event_ids, (142.30, 142.44)):
                session.add(Event(id=event_id, title="Candidate", event_type="earthquake", status="active"))
                session.add(EventGeometry(
                    event_id=event_id, geometry_type="Point",
                    geometry_json={"type": "Point", "coordinates": [longitude, 38.322]},
                    observed_at=datetime(2026, 10, 24, 15, 12, 36, tzinfo=UTC),
                ))
            session.commit()

            def handler(request: httpx.Request) -> httpx.Response:
                if "gdacs.org" in request.url.host:
                    return httpx.Response(503, json={"error": "fixture failure"})
                return httpx.Response(200, json=usgs_body)

            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                result = EarthquakeIngestionService(
                    session, providers=[GDACSProvider(client=client), USGSProvider(client=client)]
                ).run_all()
            assert [entry["status"] for entry in result["providers"]] == ["failed", "completed"]
            assert session.scalar(select(func.count()).select_from(SourceObservation)) == 1
            observation = session.scalar(select(SourceObservation))
            decision = session.scalar(select(EventCorrelation).where(EventCorrelation.observation_id == observation.id))
            evidence = session.scalars(select(EventCorrelationCandidate).where(
                EventCorrelationCandidate.correlation_id == decision.id
            )).all()
            assert observation.event_id is None and observation.match_status == "unresolved"
            assert decision.outcome == "unresolved" and decision.selected_event_id is None
            assert set(uuid.UUID(value) for value in decision.candidate_event_ids) == set(event_ids)
            assert len(evidence) == 2 and all(item.candidate_observation_id is None for item in evidence)
            assert session.scalar(select(func.count()).select_from(Event)) == 2
    finally:
        engine.dispose()


def test_mid_batch_processing_failure_rolls_back_observations_and_marks_run_failed(database_factory):
    database_url = database_factory("batch_failure")
    _alembic(database_url, "head")
    from app.models import Event, EventCorrelation, IngestionRun, Source, SourceObservation
    from app.providers.usgs import USGSProvider
    from app.services.earthquake_ingestion import EarthquakeIngestionService

    fixture = ROOT / "tests" / "fixtures" / "usgs_earthquake.json"
    feature = json.loads(fixture.read_text(encoding="utf-8"))["features"][0]

    class FailingBatchProvider:
        name = "usgs"

        def fetch(self):
            return [USGSProvider.normalize(feature), None]

        def close(self):
            pass

    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            result = EarthquakeIngestionService(session, [FailingBatchProvider()]).run_all()
            assert result["providers"][0]["status"] == "failed"
            assert session.scalar(select(func.count()).select_from(SourceObservation)) == 0
            assert session.scalar(select(func.count()).select_from(Event)) == 0
            assert session.scalar(select(func.count()).select_from(EventCorrelation)) == 0
            run = session.scalar(select(IngestionRun).join(Source).where(Source.code == "usgs"))
            assert run.status == "failed"
            assert run.records_received == 2
            assert run.records_inserted == run.records_updated == run.records_unchanged == 0
            assert "AttributeError" in run.error_message
    finally:
        engine.dispose()


def test_api_ingestion_route_persists_results_and_reports_provider_failure(database_factory, monkeypatch):
    database_url = database_factory("api_ingestion")
    _alembic(database_url, "head")
    from app.database import get_db
    from app.main import app
    from app.models import Event, EventCorrelation, IngestionRun, Source, SourceObservation
    from app.providers.gdacs import GDACSProvider
    from app.providers.usgs import USGSProvider
    import app.services.earthquake_ingestion as ingestion_module

    fixtures = ROOT / "tests" / "fixtures"
    bodies = {
        "gdacs": json.loads((fixtures / "gdacs_earthquake.json").read_text(encoding="utf-8")),
        "usgs": json.loads((fixtures / "usgs_earthquake.json").read_text(encoding="utf-8")),
    }
    fail_gdacs = {"value": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if "gdacs.org" in request.url.host:
            if fail_gdacs["value"]:
                return httpx.Response(503, json={"error": "deterministic test failure"})
            return httpx.Response(200, json=bodies["gdacs"])
        return httpx.Response(200, json=bodies["usgs"])

    engine = create_engine(database_url)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        # The API constructs the normal service itself. Only its HTTP clients
        # are replaced, so routing, dependency injection, service transactions,
        # adapters, normalization, and persistence all execute normally.
        monkeypatch.setattr(ingestion_module, "GDACSProvider", lambda: GDACSProvider(client=client))
        monkeypatch.setattr(ingestion_module, "USGSProvider", lambda: USGSProvider(client=client))
        original_overrides = app.dependency_overrides.copy()

        def test_db():
            with Session(engine) as session:
                yield session

        app.dependency_overrides[get_db] = test_db

        async def request(method: str):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as api_client:
                return await api_client.request(method, "/ingestion/earthquakes/run")

        try:
            first_response = asyncio.run(request("POST"))
            assert first_response.status_code == 200
            first_payload = first_response.json()
            assert [item["status"] for item in first_payload["providers"]] == ["completed", "completed"]
            assert [item["inserted"] for item in first_payload["providers"]] == [1, 1]

            second_response = asyncio.run(request("POST"))
            assert second_response.status_code == 200
            second_payload = second_response.json()
            assert [item["unchanged"] for item in second_payload["providers"]] == [1, 1]
            with Session(engine) as check:
                assert check.scalar(select(func.count()).select_from(SourceObservation)) == 2
                assert check.scalar(select(func.count()).select_from(Event)) == 1
                assert check.scalar(select(func.count()).select_from(EventCorrelation)) == 2

            fail_gdacs["value"] = True
            failure_response = asyncio.run(request("POST"))
            assert failure_response.status_code == 200
            failure_payload = failure_response.json()
            assert [item["status"] for item in failure_payload["providers"]] == ["failed", "completed"]
            with Session(engine) as check:
                assert check.scalar(select(func.count()).select_from(SourceObservation)) == 2
                assert check.scalar(select(func.count()).select_from(Event)) == 1
                failed_run = check.scalar(
                    select(IngestionRun).join(Source).where(
                        Source.code == "gdacs", IngestionRun.status == "failed"
                    )
                )
                successful_usgs_run = check.scalar(
                    select(IngestionRun).join(Source).where(
                        Source.code == "usgs", IngestionRun.status == "completed"
                    ).order_by(IngestionRun.finished_at.desc())
                )
                assert failed_run is not None and failed_run.records_inserted == 0
                assert successful_usgs_run is not None and successful_usgs_run.records_unchanged == 1

            # The route has no request body or query parameters; wrong methods
            # are rejected by FastAPI's existing route contract.
            assert asyncio.run(request("GET")).status_code == 405

            # Unexpected service-level exceptions are not converted into a
            # success payload; FastAPI returns its normal 500 response and the
            # route still closes the service in its finally block.
            closed = {"value": False}

            class ExplodingService:
                def __init__(self, _db):
                    pass

                def run_all(self):
                    raise RuntimeError("unexpected service failure")

                def close(self):
                    closed["value"] = True

            monkeypatch.setattr("app.main.EarthquakeIngestionService", ExplodingService)

            async def failing_request():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                    base_url="http://test",
                ) as api_client:
                    return await api_client.post("/ingestion/earthquakes/run")

            assert asyncio.run(failing_request()).status_code == 500
            assert closed["value"] is True
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(original_overrides)
            engine.dispose()
