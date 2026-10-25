from datetime import UTC, datetime, timedelta
from uuid6 import uuid7

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    EarthquakeObservation,
    Event,
    EventCorrelation,
    EventCorrelationCandidate,
    IngestionRun,
    Source,
    SourceObservation,
)
from app.providers.errors import ProviderError
from app.schemas.earthquake import NormalizedEarthquake
from app.services.earthquake_ingestion import EarthquakeIngestionService


class FakeProvider:
    def __init__(self, name, observations=None, error=None):
        self.name = name
        self.observations = observations or []
        self.error = error

    def fetch(self):
        if self.error:
            raise self.error
        return self.observations

    def close(self):
        pass


def item(provider, record_id, *, event_id=None, episode_id=None, latitude=10.0, longitude=20.0,
         magnitude=6.0, depth=10.0, status="reviewed", origin=None, raw=None):
    if event_id is None:
        event_id = record_id.split(":", 1)[0]
    if episode_id is None and ":" in record_id:
        episode_id = record_id.split(":", 1)[1]
    values = {
        "provider": provider,
        "provider_record_id": record_id,
        "provider_event_id": event_id,
        "origin_time": origin or datetime(2026, 1, 1, tzinfo=UTC),
        "latitude": latitude,
        "longitude": longitude,
        "title": "Earthquake fixture",
        "raw_payload": raw or {"provider_record_id": record_id},
    }
    if episode_id is not None:
        values["provider_episode_id"] = episode_id
    if magnitude is not None:
        values["magnitude"] = magnitude
    if depth is not None:
        values["depth_km"] = depth
    if status is not None:
        values["provider_status"] = status
    return NormalizedEarthquake(**values)


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_observations_are_idempotent_and_cross_source_events_correlate(db):
    first = FakeProvider("gdacs", [item("gdacs", "1476137:1632460", event_id="1476137", episode_id="1632460")])
    second = FakeProvider("usgs", [item("usgs", "us7000test", latitude=10.001, magnitude=6.1)])

    result = EarthquakeIngestionService(db, [first, second]).run_all()
    assert [entry["status"] for entry in result["providers"]] == ["completed", "completed"]
    assert [entry["inserted"] for entry in result["providers"]] == [1, 1]
    assert db.scalar(select(func.count()).select_from(SourceObservation)) == 2
    assert db.scalar(select(func.count()).select_from(Event)) == 1
    rows = db.scalars(select(SourceObservation).order_by(SourceObservation.provider_record_id)).all()
    assert rows[0].event_id == rows[1].event_id
    assert rows[0].provider_event_id == "1476137"
    assert rows[0].provider_episode_id == "1632460"
    assert db.scalar(select(func.count()).select_from(EventCorrelation)) == 2
    assert db.scalar(select(func.count()).select_from(EventCorrelationCandidate)) == 1

    repeated = EarthquakeIngestionService(db, [first, second]).run_all()
    assert [entry["unchanged"] for entry in repeated["providers"]] == [1, 1]
    assert db.scalar(select(func.count()).select_from(SourceObservation)) == 2
    assert db.scalar(select(func.count()).select_from(EventCorrelation)) == 2


def test_changed_values_shift_one_immediately_previous_snapshot_and_retain_event(db):
    provider = FakeProvider("usgs", [item("usgs", "us7000revision")])
    service = EarthquakeIngestionService(db, [provider])
    service.run_all()
    row = db.scalar(select(SourceObservation))
    original_event_id = row.event_id

    provider.observations = [item(
        "usgs", "us7000revision", latitude=10.2, longitude=20.2,
        magnitude=6.2, depth=12.0, status="automatic", raw={"revision": 2},
    )]
    result = service.run_all()
    detail = db.get(EarthquakeObservation, row.id)
    assert result["providers"][0]["updated"] == 1
    assert row.event_id == original_event_id
    assert db.scalar(select(func.count()).select_from(Event)) == 1
    assert (detail.previous_latitude, detail.previous_longitude) == (10.0, 20.0)
    assert (detail.latitude, detail.longitude) == (10.2, 20.2)
    assert detail.previous_magnitude == 6.0 and detail.magnitude == 6.2
    assert detail.previous_depth_km == 10.0 and detail.depth_km == 12.0
    assert detail.previous_provider_status == "reviewed" and detail.provider_status == "automatic"
    assert row.raw_payload == {"revision": 2}

    provider.observations = [item(
        "usgs", "us7000revision", latitude=10.3, longitude=20.3,
        magnitude=6.4, depth=14.0, status="reviewed", raw={"revision": 3},
    )]
    service.run_all()
    assert (detail.previous_latitude, detail.previous_longitude) == (10.2, 20.2)
    assert detail.previous_magnitude == 6.2 and detail.magnitude == 6.4


def test_unchanged_refetch_does_not_change_previous_values(db):
    provider = FakeProvider("usgs", [item("usgs", "us7000stable")])
    service = EarthquakeIngestionService(db, [provider])
    service.run_all()
    provider.observations = [item("usgs", "us7000stable", magnitude=6.2, depth=11.0, latitude=10.1, longitude=20.1)]
    service.run_all()
    detail = db.scalar(select(EarthquakeObservation))
    previous = (detail.previous_magnitude, detail.previous_depth_km, detail.previous_latitude, detail.previous_longitude)

    result = service.run_all()

    assert result["providers"][0]["unchanged"] == 1
    assert (detail.previous_magnitude, detail.previous_depth_km, detail.previous_latitude, detail.previous_longitude) == previous


def test_null_or_missing_fields_preserve_last_valid_values(db):
    provider = FakeProvider("usgs", [item("usgs", "us7000nulls")])
    service = EarthquakeIngestionService(db, [provider])
    service.run_all()
    provider.observations = [item(
        "usgs", "us7000nulls", latitude=None, longitude=None, magnitude=None, depth=None, status=None,
    )]
    service.run_all()
    detail = db.scalar(select(EarthquakeObservation))

    assert (detail.latitude, detail.longitude) == (10.0, 20.0)
    assert detail.magnitude == 6.0 and detail.depth_km == 10.0
    assert detail.provider_status == "reviewed"
    assert detail.previous_latitude is None and detail.previous_longitude is None
    assert detail.previous_magnitude is None and detail.previous_depth_km is None


def test_coordinate_change_is_an_atomic_pair(db):
    provider = FakeProvider("usgs", [item("usgs", "us7000coords")])
    service = EarthquakeIngestionService(db, [provider])
    service.run_all()
    provider.observations = [item("usgs", "us7000coords", latitude=11.0, longitude=21.0)]
    service.run_all()
    detail = db.scalar(select(EarthquakeObservation))
    assert (detail.previous_latitude, detail.previous_longitude) == (10.0, 20.0)
    assert (detail.latitude, detail.longitude) == (11.0, 21.0)


def test_one_provider_failure_does_not_block_the_other(db):
    failed = FakeProvider("gdacs", error=ProviderError("fixture timeout"))
    healthy = FakeProvider("usgs", [item("usgs", "us7000healthy")])
    result = EarthquakeIngestionService(db, [failed, healthy]).run_all()
    assert [entry["status"] for entry in result["providers"]] == ["failed", "completed"]
    failed_run = db.scalar(select(IngestionRun).join(Source).where(Source.code == "gdacs"))
    assert failed_run.status == "failed"
    assert "fixture timeout" in failed_run.error_message
    assert db.scalar(select(func.count()).select_from(SourceObservation)) == 1


def test_ambiguous_observation_is_retained_unlinked_with_candidate_evidence(db):
    providers = [
        FakeProvider("gdacs", [
            item("gdacs", "gdacs-a:1", event_id="gdacs-a", episode_id="1"),
            item("gdacs", "gdacs-b:1", event_id="gdacs-b", episode_id="1", latitude=10.8),
        ]),
        FakeProvider("usgs", [item("usgs", "usgs-between", latitude=10.4)]),
    ]
    EarthquakeIngestionService(db, providers).run_all()
    unresolved = db.scalar(select(SourceObservation).where(SourceObservation.provider_event_id == "usgs-between"))
    decision = db.scalar(select(EventCorrelation).where(EventCorrelation.observation_id == unresolved.id))
    evidence = db.scalars(select(EventCorrelationCandidate).where(EventCorrelationCandidate.correlation_id == decision.id)).all()
    assert unresolved.match_status == "unresolved" and unresolved.event_id is None
    assert decision.outcome == "unresolved"
    assert len(decision.candidate_event_ids) == 2
    assert len(evidence) == 2 and all(item.candidate_observation_id is not None for item in evidence)


def test_exact_legacy_source_identity_reuses_canonical_event(db):
    legacy_id = uuid7()
    db.add(Event(id=legacy_id, legacy_source="USGS", legacy_external_id="us7000legacy", title="Existing", event_type="earthquake"))
    db.commit()
    provider = FakeProvider("usgs", [item("usgs", "us7000legacy")])
    EarthquakeIngestionService(db, [provider]).run_all()

    observation = db.scalar(select(SourceObservation))
    decision = db.scalar(select(EventCorrelation))
    assert db.scalar(select(func.count()).select_from(Event)) == 1
    assert observation.event_id == legacy_id
    assert decision.method == "exact_legacy_provider_identity"


def test_unassessable_legacy_event_blocks_speculative_event_creation(db):
    db.add(Event(legacy_source="unknown", legacy_external_id="legacy-1", title="Legacy EQ", event_type="earthquake"))
    db.commit()
    provider = FakeProvider("usgs", [item("usgs", "us7000new")])
    EarthquakeIngestionService(db, [provider]).run_all()

    observation = db.scalar(select(SourceObservation))
    decision = db.scalar(select(EventCorrelation))
    assert db.scalar(select(func.count()).select_from(Event)) == 1
    assert observation.event_id is None and observation.match_status == "unresolved"
    assert decision.outcome == "unresolved"
    assert "Legacy earthquake events" in decision.rationale


def test_expired_raw_payload_is_removed_without_deleting_normalized_observation(db):
    provider = FakeProvider("usgs", [item("usgs", "us7000expiry")])
    EarthquakeIngestionService(db, [provider]).run_all()
    observation = db.scalar(select(SourceObservation))
    assert observation.raw_payload is not None
    expiry = observation.raw_payload_expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    assert expiry > datetime.now(UTC)
    observation.raw_payload_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    EarthquakeIngestionService(db, [FakeProvider("usgs", [])]).run_all()

    assert observation.raw_payload is None
    assert db.scalar(select(func.count()).select_from(SourceObservation)) == 1
    assert db.scalar(select(EarthquakeObservation).where(EarthquakeObservation.observation_id == observation.id)) is not None
