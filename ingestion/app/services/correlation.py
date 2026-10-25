from dataclasses import dataclass
from datetime import UTC, datetime
from math import asin, cos, radians, sin, sqrt
from typing import Iterable
from uuid import UUID

from app.schemas.earthquake import NormalizedEarthquake

MAX_TIME_DELTA_SECONDS = 120.0
MAX_DISTANCE_KM = 75.0
MIN_AUTO_LINK_CONFIDENCE = 0.65


@dataclass(frozen=True)
class CandidateObservation:
    event_id: UUID
    origin_time: datetime
    latitude: float | None
    longitude: float | None
    magnitude: float | None
    observation_id: UUID | None = None


@dataclass(frozen=True)
class CorrelationEvidence:
    event_id: UUID
    observation_id: UUID | None
    confidence: float
    distance_km: float | None
    time_delta_seconds: float | None
    selected: bool = False


@dataclass(frozen=True)
class CorrelationResult:
    outcome: str
    selected_event_id: UUID | None
    candidate_event_ids: list[UUID]
    rationale: str
    confidence: float
    distance_km: float | None = None
    time_delta_seconds: float | None = None
    evidence: list[CorrelationEvidence] | None = None


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance using the haversine formula."""
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371.0088 * 2 * asin(sqrt(a))


def correlate(observation: NormalizedEarthquake, candidates: Iterable[CandidateObservation]) -> CorrelationResult:
    """Match only a unique, sufficiently strong time-and-space candidate."""
    best_by_event: dict[UUID, tuple[float, float, float, float | None, UUID | None]] = {}
    all_evidence: list[CorrelationEvidence] = []
    for candidate in candidates:
        if observation.latitude is None or observation.longitude is None:
            continue
        if candidate.latitude is None or candidate.longitude is None:
            continue
        candidate_time = candidate.origin_time
        if candidate_time.tzinfo is None:
            candidate_time = candidate_time.replace(tzinfo=UTC)
        time_delta = abs((observation.origin_time - candidate_time).total_seconds())
        distance = distance_km(observation.latitude, observation.longitude, candidate.latitude, candidate.longitude)
        if time_delta > MAX_TIME_DELTA_SECONDS or distance > MAX_DISTANCE_KM:
            continue
        time_score = 1 - time_delta / MAX_TIME_DELTA_SECONDS
        distance_score = 1 - distance / MAX_DISTANCE_KM
        score = 0.55 * time_score + 0.40 * distance_score
        magnitude_delta = None
        if observation.magnitude is not None and candidate.magnitude is not None:
            magnitude_delta = abs(observation.magnitude - candidate.magnitude)
            score += 0.05 * max(0.0, 1 - magnitude_delta / 2.0)
        all_evidence.append(CorrelationEvidence(candidate.event_id, candidate.observation_id, score, distance, time_delta))
        previous = best_by_event.get(candidate.event_id)
        if previous is None or score > previous[0]:
            best_by_event[candidate.event_id] = (score, distance, time_delta, magnitude_delta, candidate.observation_id)

    if not best_by_event:
        return CorrelationResult("created", None, [], "No existing event fell within 120 seconds and 75 km; created a new canonical event.", 0.0)

    ranked = sorted(best_by_event.items(), key=lambda item: item[1][0], reverse=True)
    ids = [event_id for event_id, _ in ranked]
    if len(ranked) != 1:
        return CorrelationResult("unresolved", None, ids, f"Multiple canonical events satisfy the time and distance windows; manual review required ({len(ids)} candidates).", 0.0, evidence=all_evidence)

    event_id, (score, distance, delta, magnitude_delta, best_observation_id) = ranked[0]
    rationale = f"Unique candidate: origin times differ by {delta:.1f}s and epicenters by {distance:.2f}km (limits 120s/75km)."
    if magnitude_delta is not None:
        rationale += f" Magnitudes differ by {magnitude_delta:.2f}; magnitude is supporting evidence only."
    if score < MIN_AUTO_LINK_CONFIDENCE:
        return CorrelationResult("unresolved", None, ids, rationale + f" Confidence {score:.3f} is below the {MIN_AUTO_LINK_CONFIDENCE:.2f} auto-link threshold.", score, distance, delta, all_evidence)
    evidence = [
        CorrelationEvidence(item.event_id, item.observation_id, item.confidence, item.distance_km, item.time_delta_seconds,
                            item.event_id == event_id and item.observation_id == best_observation_id)
        for item in all_evidence
    ]
    return CorrelationResult("linked", event_id, ids, rationale, score, distance, delta, evidence)
