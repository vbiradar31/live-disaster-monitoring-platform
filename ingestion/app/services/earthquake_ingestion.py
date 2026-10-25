from datetime import UTC, datetime, timedelta
from uuid6 import uuid7

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    EarthquakeObservation,
    Event,
    EventCorrelation,
    EventCorrelationCandidate,
    EventGeometry,
    IngestionRun,
    Source,
    SourceObservation,
)
from app.providers import GDACSProvider, USGSProvider
from app.schemas.earthquake import NormalizedEarthquake
from app.services.correlation import CandidateObservation, CorrelationEvidence, CorrelationResult, correlate

RAW_PAYLOAD_RETENTION_DAYS = 90
CORRELATION_METHOD = "origin_time_distance_magnitude_support"
CORRELATION_METHOD_VERSION = "1"


class EarthquakeIngestionService:
    def __init__(self, db: Session, providers=None):
        self.db = db
        self.providers = providers if providers is not None else [GDACSProvider(), USGSProvider()]

    def close(self) -> None:
        for provider in self.providers:
            provider.close()

    def run_all(self) -> dict:
        results = []
        self._expire_raw_payloads()
        for adapter in self.providers:
            results.append(self._run_provider(adapter))
        return {"providers": results}

    def _expire_raw_payloads(self) -> None:
        now = datetime.now(UTC)
        self.db.execute(
            update(SourceObservation)
            .where(SourceObservation.raw_payload_expires_at <= now)
            .values(raw_payload=None, raw_payload_expires_at=None)
            .execution_options(synchronize_session=False)
        )
        self.db.commit()

    def _run_provider(self, adapter) -> dict:
        source = self.db.scalar(select(Source).where(Source.code == adapter.name))
        if source is None:
            source = Source(code=adapter.name, name=adapter.name.upper())
            try:
                with self.db.begin_nested():
                    self.db.add(source)
                    self.db.flush()
            except IntegrityError:
                source = self.db.scalar(select(Source).where(Source.code == adapter.name))
                if source is None:
                    raise

        run = IngestionRun(source_id=source.id, status="running")
        self.db.add(run)
        self.db.commit()
        run_id, source_id = run.id, source.id
        received_count = 0

        try:
            observations = adapter.fetch()
            received_count = len(observations)
            run.records_received = received_count
            counts = {"inserted": 0, "updated": 0, "unchanged": 0}
            for normalized in observations:
                outcome = self._persist_observation(source_id, run_id, normalized)
                counts[outcome] += 1
            run.records_inserted = counts["inserted"]
            run.records_updated = counts["updated"]
            run.records_unchanged = counts["unchanged"]
            run.status = "completed"
            run.finished_at = datetime.now(UTC)
            # Commit a provider's observations and its successful run summary
            # atomically. A later bad record must not leave a misleading partial run.
            self.db.commit()
            return {"provider": adapter.name, "status": "completed", "received": len(observations), **counts}
        except Exception as exc:
            self.db.rollback()
            run = self.db.get(IngestionRun, run_id)
            run.records_received = received_count
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
            run.error_message = f"{type(exc).__name__}: {exc}"[:2000]
            self.db.commit()
            return {"provider": adapter.name, "status": "failed", "error": run.error_message}

    def _persist_observation(self, source_id, run_id, normalized: NormalizedEarthquake) -> str:
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=RAW_PAYLOAD_RETENTION_DAYS)
        row = self.db.scalar(select(SourceObservation).where(
            SourceObservation.source_id == source_id,
            SourceObservation.provider_record_id == normalized.provider_record_id,
        ).with_for_update())
        created = row is None
        if created:
            row = SourceObservation(
                source_id=source_id,
                provider_record_id=normalized.provider_record_id,
                provider_event_id=normalized.provider_event_id,
                provider_episode_id=normalized.provider_episode_id,
                hazard_type="earthquake",
                first_ingestion_run_id=run_id,
                last_ingestion_run_id=run_id,
                provider_updated_at=normalized.provider_updated_at,
                first_seen_at=now,
                last_seen_at=now,
                provider_title=normalized.title,
                raw_payload=normalized.raw_payload,
                raw_payload_expires_at=expires_at,
                match_status="unresolved",
            )
            try:
                with self.db.begin_nested():
                    self.db.add(row)
                    self.db.flush()
            except IntegrityError:
                # A concurrent run may have inserted the same provider record.
                row = self.db.scalar(select(SourceObservation).where(
                    SourceObservation.source_id == source_id,
                    SourceObservation.provider_record_id == normalized.provider_record_id,
                ).with_for_update())
                if row is None:
                    raise
                created = False

        if not created:
            return self._refresh_observation(row, run_id, normalized, now, expires_at)

        detail = EarthquakeObservation(
            observation_id=row.id,
            origin_time=normalized.origin_time,
            latitude=normalized.latitude,
            longitude=normalized.longitude,
            magnitude=normalized.magnitude,
            depth_km=normalized.depth_km,
            provider_status=normalized.provider_status,
        )
        self.db.add(detail)
        self.db.flush()
        self._decide_link(row, detail, normalized)
        self.db.flush()
        return "inserted"

    def _refresh_observation(self, row, run_id, normalized, now, expires_at) -> str:
        detail = self.db.get(EarthquakeObservation, row.id)
        changed = False
        row.last_ingestion_run_id = run_id
        row.last_seen_at = now
        if normalized.provider_updated_at is not None and not self._same_datetime(
            normalized.provider_updated_at, row.provider_updated_at
        ):
            changed = True
        row.provider_updated_at = normalized.provider_updated_at or row.provider_updated_at
        if normalized.title is not None and normalized.title != row.provider_title:
            changed = True
        row.provider_title = normalized.title or row.provider_title
        row.raw_payload = normalized.raw_payload
        row.raw_payload_expires_at = expires_at

        stored_origin_time = detail.origin_time
        if stored_origin_time.tzinfo is None:
            stored_origin_time = stored_origin_time.replace(tzinfo=UTC)
        if stored_origin_time != normalized.origin_time:
            detail.origin_time = normalized.origin_time
            changed = True
        changed |= self._shift_scalar(detail, "magnitude", "previous_magnitude", normalized.magnitude)
        changed |= self._shift_scalar(detail, "depth_km", "previous_depth_km", normalized.depth_km)
        changed |= self._shift_scalar(detail, "provider_status", "previous_provider_status", normalized.provider_status)

        if normalized.latitude is not None and normalized.longitude is not None:
            if (detail.latitude, detail.longitude) != (normalized.latitude, normalized.longitude):
                detail.previous_latitude, detail.previous_longitude = detail.latitude, detail.longitude
                detail.latitude, detail.longitude = normalized.latitude, normalized.longitude
                changed = True

        if not changed:
            return "unchanged"

        if row.event_id is not None:
            self._record_correlation(
                row,
                outcome="retained",
                selected_event_id=row.event_id,
                candidate_event_ids=[row.event_id],
                rationale="A provider revision retained the established canonical link because its provider record identity is unchanged.",
                confidence=1.0,
                method="stable_provider_record_id",
                method_version="1",
                evidence=[CorrelationEvidence(row.event_id, row.id, 1.0, None, None, True)],
            )
        else:
            self._decide_link(row, detail, normalized)
        self.db.flush()
        return "updated"

    @staticmethod
    def _shift_scalar(detail, current_name: str, previous_name: str, incoming) -> bool:
        # Null/missing source fields do not erase the last valid normalized value.
        if incoming is None:
            return False
        current = getattr(detail, current_name)
        if current == incoming:
            return False
        setattr(detail, previous_name, current)
        setattr(detail, current_name, incoming)
        return True

    @staticmethod
    def _same_datetime(left, right) -> bool:
        if left is None or right is None:
            return left is right
        if left.tzinfo is None:
            left = left.replace(tzinfo=UTC)
        if right.tzinfo is None:
            right = right.replace(tzinfo=UTC)
        return left == right

    def _decide_link(self, row, detail, normalized) -> None:
        if self.db.bind.dialect.name == "postgresql":
            # Serialize candidate selection and event creation across concurrent
            # source runs so two reports of one earthquake cannot create twins.
            self.db.execute(select(func.pg_advisory_xact_lock(20261025)))
        exact_legacy = self.db.scalars(
            select(Event).where(
                func.lower(Event.legacy_source) == normalized.provider,
                Event.legacy_external_id.in_({normalized.provider_event_id, normalized.provider_record_id}),
            )
        ).all()
        if len(exact_legacy) == 1:
            event = exact_legacy[0]
            row.event_id = event.id
            row.match_status = "linked"
            self._record_correlation(
                row,
                outcome="linked",
                selected_event_id=event.id,
                candidate_event_ids=[event.id],
                rationale="Linked by exact legacy provider and provider event ID; no identifier was inferred.",
                confidence=1.0,
                method="exact_legacy_provider_identity",
                method_version="1",
                evidence=[CorrelationEvidence(event.id, None, 1.0, None, None, True)],
            )
            return
        if len(exact_legacy) > 1:
            self._save_unresolved(row, [event.id for event in exact_legacy], "Multiple legacy events have the same source and provider ID; review required.", "exact_legacy_provider_identity")
            return

        if detail.latitude is None or detail.longitude is None:
            self._save_unresolved(
                row, [], "No usable coordinate pair was supplied; canonical event creation is deferred.",
                "missing_location_review",
            )
            return

        legacy_geometry_candidates = self._legacy_geometry_candidates(normalized)
        candidates = self._observation_candidates(normalized, legacy_geometry_candidates)
        result = correlate(normalized, candidates)
        unknown_legacy = self._unassessed_legacy_event_ids()
        if result.outcome == "created" and unknown_legacy:
            self._save_unresolved(
                row,
                unknown_legacy,
                "Legacy earthquake events without source observations or usable geometry remain unassessed; automatic event creation is paused to avoid duplicates.",
                "legacy_event_safety_review",
            )
            return
        if result.outcome == "linked" and result.evidence and any(
            item.selected and item.observation_id is None for item in result.evidence
        ):
            self._save_unresolved(
                row, result.candidate_event_ids,
                "A legacy event geometry is a plausible candidate, but it has no source observation; review required before linking.",
                "legacy_event_geometry_review",
                result.evidence,
            )
            return
        if result.outcome == "created":
            event_id = uuid7()
            self.db.add(Event(
                id=event_id,
                title=normalized.title or "Earthquake",
                description=None,
                event_type="earthquake",
                status="active",
            ))
            self.db.flush()
            row.event_id = event_id
            row.match_status = "linked"
            if detail.latitude is not None and detail.longitude is not None:
                coordinates = [detail.longitude, detail.latitude]
                if detail.depth_km is not None:
                    coordinates.append(detail.depth_km)
                self.db.add(EventGeometry(
                    event_id=event_id,
                    geometry_type="Point",
                    geometry_json={"type": "Point", "coordinates": coordinates},
                    observed_at=detail.origin_time,
                ))
            self._record_correlation(
                row,
                outcome="created",
                selected_event_id=event_id,
                candidate_event_ids=[],
                rationale=result.rationale,
                confidence=result.confidence,
                method=CORRELATION_METHOD,
                method_version=CORRELATION_METHOD_VERSION,
                evidence=[],
            )
            return
        if result.outcome == "linked":
            row.event_id = result.selected_event_id
            row.match_status = "linked"
        else:
            row.match_status = "unresolved"
        self._record_correlation(
            row,
            outcome=result.outcome,
            selected_event_id=result.selected_event_id,
            candidate_event_ids=result.candidate_event_ids,
            rationale=result.rationale,
            confidence=result.confidence,
            method=CORRELATION_METHOD,
            method_version=CORRELATION_METHOD_VERSION,
            evidence=result.evidence or [],
        )

    def _observation_candidates(self, normalized, legacy_geometry_candidates=None) -> list[CandidateObservation]:
        if normalized.origin_time is None:
            return []
        window = timedelta(seconds=120)
        rows = self.db.execute(
            select(SourceObservation, EarthquakeObservation)
            .join(EarthquakeObservation, EarthquakeObservation.observation_id == SourceObservation.id)
            .where(
                SourceObservation.event_id.is_not(None),
                EarthquakeObservation.origin_time >= normalized.origin_time - window,
                EarthquakeObservation.origin_time <= normalized.origin_time + window,
            )
        ).all()
        candidates = [
            CandidateObservation(
                event_id=row.event_id,
                observation_id=row.id,
                origin_time=detail.origin_time,
                latitude=detail.latitude,
                longitude=detail.longitude,
                magnitude=detail.magnitude,
            )
            for row, detail in rows
        ]
        candidates.extend(legacy_geometry_candidates or [])
        return candidates

    def _legacy_geometry_candidates(self, normalized) -> list[CandidateObservation]:
        if normalized.origin_time is None:
            return []
        window = timedelta(seconds=120)
        geometries = self.db.execute(
            select(EventGeometry, Event)
            .join(Event, Event.id == EventGeometry.event_id)
            .where(
                func.lower(Event.event_type).in_(("earthquake", "eq")),
                EventGeometry.observed_at >= normalized.origin_time - window,
                EventGeometry.observed_at <= normalized.origin_time + window,
                ~select(SourceObservation.id).where(SourceObservation.event_id == Event.id).exists(),
            )
        ).all()
        candidates = []
        for geometry, event in geometries:
            payload = geometry.geometry_json or {}
            coords = payload.get("coordinates") or []
            if payload.get("type") != "Point" or len(coords) < 2:
                continue
            candidates.append(CandidateObservation(
                event_id=event.id,
                observation_id=None,
                origin_time=geometry.observed_at,
                longitude=coords[0],
                latitude=coords[1],
                magnitude=None,
            ))
        return candidates

    def _unassessed_legacy_event_ids(self) -> list:
        # Events with neither a source observation nor usable time/location
        # evidence cannot be safely ruled out as duplicates.
        event_ids = self.db.scalars(
            select(Event.id)
            .where(func.lower(Event.event_type).in_(("earthquake", "eq")))
            .where(~select(SourceObservation.id).where(SourceObservation.event_id == Event.id).exists())
        ).all()
        if not event_ids:
            return []
        geometries = self.db.execute(
            select(EventGeometry.event_id, EventGeometry.geometry_json, EventGeometry.observed_at)
            .where(EventGeometry.event_id.in_(event_ids))
            .where(EventGeometry.geometry_type == "Point")
            .where(EventGeometry.observed_at.is_not(None))
        ).all()
        usable = set()
        for event_id, payload, _observed_at in geometries:
            coordinates = (payload or {}).get("coordinates") or []
            if (payload or {}).get("type") == "Point" and len(coordinates) >= 2:
                usable.add(event_id)
        return [event_id for event_id in event_ids if event_id not in usable]

    def _save_unresolved(self, row, event_ids, rationale, method, evidence=None) -> None:
        row.event_id = None
        row.match_status = "unresolved"
        review_evidence = evidence if evidence is not None else [
            CorrelationEvidence(event_id, None, 0.0, None, None) for event_id in event_ids
        ]
        review_evidence = [
            CorrelationEvidence(
                item.event_id, item.observation_id, item.confidence,
                item.distance_km, item.time_delta_seconds, False,
            )
            for item in review_evidence
        ]
        self._record_correlation(
            row,
            outcome="unresolved",
            selected_event_id=None,
            candidate_event_ids=event_ids,
            rationale=rationale,
            confidence=0.0,
            method=method,
            method_version="1",
            evidence=review_evidence,
        )

    def _record_correlation(
        self, row, *, outcome, selected_event_id, candidate_event_ids,
        rationale, confidence, method, method_version, evidence,
    ) -> None:
        correlation = EventCorrelation(
            observation_id=row.id,
            outcome=outcome,
            selected_event_id=selected_event_id,
            candidate_event_ids=[str(value) for value in candidate_event_ids],
            method=method,
            method_version=method_version,
            rationale=rationale,
            confidence=confidence,
        )
        self.db.add(correlation)
        self.db.flush()
        for item in evidence:
            self.db.add(EventCorrelationCandidate(
                correlation_id=correlation.id,
                candidate_event_id=item.event_id,
                candidate_observation_id=item.observation_id,
                distance_km=item.distance_km,
                time_delta_seconds=item.time_delta_seconds,
                confidence=item.confidence,
                selected=item.selected,
            ))
