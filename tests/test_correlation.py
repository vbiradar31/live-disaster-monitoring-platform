from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.schemas.earthquake import NormalizedEarthquake
from app.services.correlation import CandidateObservation, correlate, distance_km


def observation(**changes):
    values = {
        "provider": "usgs",
        "provider_record_id": "us-test",
        "provider_event_id": "us-test",
        "origin_time": datetime(2026, 1, 1, tzinfo=UTC),
        "latitude": 10.0,
        "longitude": 20.0,
        "magnitude": 6.0,
        "depth_km": 10.0,
        "raw_payload": {"id": "us-test"},
    }
    values.update(changes)
    return NormalizedEarthquake(**values)


def candidate(event_id, **changes):
    values = {
        "event_id": event_id,
        "origin_time": datetime(2026, 1, 1, tzinfo=UTC),
        "latitude": 10.0,
        "longitude": 20.0,
        "magnitude": 6.1,
    }
    values.update(changes)
    return CandidateObservation(**values)


def test_unique_close_candidate_links_and_records_supporting_magnitude():
    event_id = uuid4()
    result = correlate(observation(), [candidate(event_id)])

    assert result.outcome == "linked"
    assert result.selected_event_id == event_id
    assert result.confidence >= 0.65
    assert "Magnitudes differ" in result.rationale


def test_candidates_outside_time_or_distance_window_do_not_match():
    result = correlate(observation(), [candidate(uuid4(), origin_time=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=3))])

    assert result.outcome == "created"
    assert result.candidate_event_ids == []


def test_multiple_plausible_events_remain_unresolved():
    first, second = uuid4(), uuid4()
    result = correlate(observation(), [candidate(first), candidate(second, latitude=10.001)])

    assert result.outcome == "unresolved"
    assert result.selected_event_id is None
    assert set(result.candidate_event_ids) == {first, second}
    assert "manual review" in result.rationale


def test_magnitude_alone_never_creates_a_match():
    result = correlate(observation(magnitude=7.0), [candidate(uuid4(), latitude=-20.0, longitude=140.0, magnitude=7.0)])

    assert result.outcome == "created"


def test_distance_uses_great_circle_and_wraps_date_line():
    assert distance_km(0, 179.9, 0, -179.9) < 25
