from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid6 import uuid7

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Source(Base):
    """Authoritative registry of external data providers."""

    __tablename__ = "sources"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    runs: Mapped[list["IngestionRun"]] = relationship(back_populates="source")


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    records_received: Mapped[int] = mapped_column(default=0, nullable=False)
    records_inserted: Mapped[int] = mapped_column(default=0, nullable=False)
    records_updated: Mapped[int] = mapped_column(default=0, nullable=False)
    records_unchanged: Mapped[int] = mapped_column(default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)

    source: Mapped[Source] = relationship(back_populates="runs")


class SourceObservation(Base):
    """Latest generic state and provenance for one provider-specific record."""

    __tablename__ = "source_observations"
    __table_args__ = (
        UniqueConstraint("source_id", "provider_record_id", name="uq_observation_source_record"),
        CheckConstraint("hazard_type <> ''", name="ck_source_observation_hazard_type"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False, index=True)
    provider_record_id: Mapped[str] = mapped_column(String(250), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_episode_id: Mapped[str | None] = mapped_column(String(200))
    hazard_type: Mapped[str] = mapped_column(String(60), nullable=False)
    first_ingestion_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    last_ingestion_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id", ondelete="RESTRICT"), index=True)
    provider_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    provider_title: Mapped[str | None] = mapped_column(String(300))
    raw_payload: Mapped[dict | None] = mapped_column(JSONB().with_variant(JSON(), "sqlite"))
    raw_payload_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    match_status: Mapped[str] = mapped_column(String(20), default="unresolved", nullable=False)

    event: Mapped["Event | None"] = relationship()
    earthquake: Mapped["EarthquakeObservation | None"] = relationship(
        back_populates="observation", cascade="all, delete-orphan", uselist=False
    )
    decisions: Mapped[list["EventCorrelation"]] = relationship(
        back_populates="observation", passive_deletes="all"
    )
    episode_links: Mapped[list["EpisodeObservation"]] = relationship(
        back_populates="observation", passive_deletes="all"
    )


class EarthquakeObservation(Base):
    """Current earthquake values with one immediately previous changed value."""

    __tablename__ = "earthquake_observations"
    __table_args__ = (
        CheckConstraint("latitude IS NULL OR latitude BETWEEN -90 AND 90", name="ck_eq_latitude_range"),
        CheckConstraint("longitude IS NULL OR longitude BETWEEN -180 AND 180", name="ck_eq_longitude_range"),
        CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR (latitude IS NOT NULL AND longitude IS NOT NULL)",
            name="ck_eq_coordinate_pair",
        ),
        CheckConstraint(
            "(previous_latitude IS NULL AND previous_longitude IS NULL) OR "
            "(previous_latitude IS NOT NULL AND previous_longitude IS NOT NULL)",
            name="ck_eq_previous_coordinate_pair",
        ),
        CheckConstraint("previous_latitude IS NULL OR previous_latitude BETWEEN -90 AND 90", name="ck_eq_previous_latitude_range"),
        CheckConstraint("previous_longitude IS NULL OR previous_longitude BETWEEN -180 AND 180", name="ck_eq_previous_longitude_range"),
    )

    observation_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_observations.id", ondelete="RESTRICT"), primary_key=True
    )
    origin_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    magnitude: Mapped[float | None] = mapped_column(Float)
    depth_km: Mapped[float | None] = mapped_column(Float)
    provider_status: Mapped[str | None] = mapped_column(String(80))
    previous_latitude: Mapped[float | None] = mapped_column(Float)
    previous_longitude: Mapped[float | None] = mapped_column(Float)
    previous_magnitude: Mapped[float | None] = mapped_column(Float)
    previous_depth_km: Mapped[float | None] = mapped_column(Float)
    previous_provider_status: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    observation: Mapped[SourceObservation] = relationship(back_populates="earthquake")


class EpisodeObservation(Base):
    """Link source observations to meaningful canonical event episodes."""

    __tablename__ = "episode_observations"
    __table_args__ = (
        UniqueConstraint("episode_id", "observation_id", name="uq_episode_observation"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)
    episode_id: Mapped[UUID] = mapped_column(ForeignKey("event_episodes.id", ondelete="RESTRICT"), nullable=False)
    observation_id: Mapped[UUID] = mapped_column(ForeignKey("source_observations.id", ondelete="RESTRICT"), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    episode: Mapped["EventEpisode"] = relationship(back_populates="observation_links")
    observation: Mapped[SourceObservation] = relationship(back_populates="episode_links")


class EventCorrelation(Base):
    """Append-only correlation decision for a source observation."""

    __tablename__ = "event_correlations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)
    observation_id: Mapped[UUID] = mapped_column(ForeignKey("source_observations.id", ondelete="RESTRICT"), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    selected_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id", ondelete="RESTRICT"))
    candidate_event_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    method: Mapped[str] = mapped_column(String(80), nullable=False)
    method_version: Mapped[str] = mapped_column(String(40), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    distance_km: Mapped[float | None] = mapped_column(Float)
    time_delta_seconds: Mapped[float | None] = mapped_column(Float)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    observation: Mapped[SourceObservation] = relationship(back_populates="decisions")
    candidates: Mapped[list["EventCorrelationCandidate"]] = relationship(
        back_populates="correlation", cascade="all, delete-orphan", passive_deletes=True
    )


class EventCorrelationCandidate(Base):
    """A defensible candidate-evidence row; a null observation denotes a legacy event."""

    __tablename__ = "event_correlation_candidates"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)
    correlation_id: Mapped[UUID] = mapped_column(ForeignKey("event_correlations.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="RESTRICT"), nullable=False, index=True)
    candidate_observation_id: Mapped[UUID | None] = mapped_column(ForeignKey("source_observations.id", ondelete="RESTRICT"), index=True)
    distance_km: Mapped[float | None] = mapped_column(Float)
    time_delta_seconds: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    correlation: Mapped[EventCorrelation] = relationship(back_populates="candidates")
