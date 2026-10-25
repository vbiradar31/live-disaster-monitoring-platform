"""Reconcile source identity, mutable earthquake details, and correlation history.

Revision ID: b4a2d91f63e8
Revises: 9f3b7e21a6c4
Create Date: 2026-10-25

This is intentionally forward-only: several new relationships and current/previous
values cannot be represented losslessly by the prior schema.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b4a2d91f63e8"
down_revision: Union[str, Sequence[str], None] = "9f3b7e21a6c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Keep the existing registry rows and all dependent FK values.
    op.rename_table("source_providers", "sources")
    op.drop_index("ix_ingestion_runs_provider_id", table_name="ingestion_runs")
    op.alter_column("ingestion_runs", "provider_id", new_column_name="source_id")
    op.drop_constraint("ingestion_runs_provider_id_fkey", "ingestion_runs", type_="foreignkey")
    op.create_foreign_key(
        "fk_ingestion_runs_source", "ingestion_runs", "sources", ["source_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_index("ix_ingestion_runs_source_id", "ingestion_runs", ["source_id"])
    op.add_column("ingestion_runs", sa.Column("records_updated", sa.Integer(), nullable=False, server_default="0"))
    op.alter_column("ingestion_runs", "records_duplicate", new_column_name="records_unchanged")
    op.alter_column("ingestion_runs", "records_updated", server_default=None)

    # Canonical event UUIDs are the only new event identity. Preserve legacy
    # provider values in explicitly named, non-unique fields for exact matching.
    op.drop_index("ix_events_external_id", table_name="events")
    op.alter_column("events", "external_id", new_column_name="legacy_external_id", nullable=True)
    op.alter_column("events", "source", new_column_name="legacy_source", nullable=True)
    op.create_index("ix_events_legacy_identity", "events", ["legacy_source", "legacy_external_id"])

    op.drop_index("ix_source_observations_provider_id", table_name="source_observations")
    op.drop_index("ix_source_observations_ingestion_run_id", table_name="source_observations")
    op.drop_index("ix_source_observations_origin_time", table_name="source_observations")
    op.alter_column("source_observations", "provider_id", new_column_name="source_id")
    op.alter_column("source_observations", "provider_event_id", new_column_name="provider_record_id")
    op.alter_column("source_observations", "ingestion_run_id", new_column_name="first_ingestion_run_id")
    op.alter_column("source_observations", "created_at", new_column_name="first_seen_at")
    op.drop_constraint("source_observations_provider_id_fkey", "source_observations", type_="foreignkey")
    op.drop_constraint("source_observations_ingestion_run_id_fkey", "source_observations", type_="foreignkey")
    op.drop_constraint("source_observations_event_id_fkey", "source_observations", type_="foreignkey")
    op.add_column("source_observations", sa.Column("provider_event_id", sa.String(length=200), nullable=True))
    op.add_column("source_observations", sa.Column("provider_episode_id", sa.String(length=200), nullable=True))
    op.add_column("source_observations", sa.Column("hazard_type", sa.String(length=60), nullable=True))
    op.add_column("source_observations", sa.Column("last_ingestion_run_id", sa.UUID(), nullable=True))
    op.add_column("source_observations", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("source_observations", sa.Column("provider_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("source_observations", sa.Column("provider_title", sa.String(length=300), nullable=True))
    op.add_column(
        "source_observations",
        sa.Column("raw_payload_expires_at", sa.DateTime(timezone=True), nullable=True,
                  server_default=sa.text("CURRENT_TIMESTAMP + INTERVAL '90 days'")),
    )
    op.alter_column("source_observations", "raw_payload", type_=postgresql.JSONB(),
                    postgresql_using="raw_payload::jsonb", nullable=True)
    op.execute(sa.text("""
        UPDATE source_observations AS o
        SET provider_event_id = CASE
                WHEN s.code = 'gdacs' THEN split_part(o.provider_record_id, ':', 1)
                ELSE o.provider_record_id
            END,
            provider_episode_id = CASE
                WHEN s.code = 'gdacs' AND position(':' in o.provider_record_id) > 0
                THEN split_part(o.provider_record_id, ':', 2)
                ELSE NULL
            END,
            hazard_type = 'earthquake',
            last_ingestion_run_id = o.first_ingestion_run_id,
            last_seen_at = o.first_seen_at,
            provider_title = o.title
        FROM sources AS s
        WHERE s.id = o.source_id
    """))
    op.alter_column("source_observations", "provider_event_id", nullable=False)
    op.alter_column("source_observations", "hazard_type", nullable=False)
    op.alter_column("source_observations", "last_ingestion_run_id", nullable=False)
    op.alter_column("source_observations", "last_seen_at", nullable=False)
    op.alter_column("source_observations", "raw_payload_expires_at", server_default=None)
    op.create_foreign_key(
        "fk_source_observations_last_run", "source_observations", "ingestion_runs",
        ["last_ingestion_run_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_source_observations_source", "source_observations", "sources",
        ["source_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_source_observations_first_run", "source_observations", "ingestion_runs",
        ["first_ingestion_run_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_source_observations_event", "source_observations", "events",
        ["event_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index("ix_source_observations_source_id", "source_observations", ["source_id"])
    op.create_index("ix_source_observations_first_ingestion_run_id", "source_observations", ["first_ingestion_run_id"])
    op.create_index("ix_source_observations_last_ingestion_run_id", "source_observations", ["last_ingestion_run_id"])
    op.create_index("ix_source_observations_raw_payload_expires_at", "source_observations", ["raw_payload_expires_at"])
    op.create_check_constraint("ck_source_observation_hazard_type", "source_observations", "hazard_type <> ''")
    op.drop_constraint("uq_observation_provider_event", "source_observations", type_="unique")
    op.create_unique_constraint(
        "uq_observation_source_record", "source_observations", ["source_id", "provider_record_id"]
    )

    op.create_table(
        "earthquake_observations",
        sa.Column("observation_id", sa.UUID(), nullable=False),
        sa.Column("origin_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("magnitude", sa.Float(), nullable=True),
        sa.Column("depth_km", sa.Float(), nullable=True),
        sa.Column("provider_status", sa.String(length=80), nullable=True),
        sa.Column("previous_latitude", sa.Float(), nullable=True),
        sa.Column("previous_longitude", sa.Float(), nullable=True),
        sa.Column("previous_magnitude", sa.Float(), nullable=True),
        sa.Column("previous_depth_km", sa.Float(), nullable=True),
        sa.Column("previous_provider_status", sa.String(length=80), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("latitude IS NULL OR latitude BETWEEN -90 AND 90", name="ck_eq_latitude_range"),
        sa.CheckConstraint("longitude IS NULL OR longitude BETWEEN -180 AND 180", name="ck_eq_longitude_range"),
        sa.CheckConstraint("(latitude IS NULL AND longitude IS NULL) OR (latitude IS NOT NULL AND longitude IS NOT NULL)", name="ck_eq_coordinate_pair"),
        sa.CheckConstraint("(previous_latitude IS NULL AND previous_longitude IS NULL) OR (previous_latitude IS NOT NULL AND previous_longitude IS NOT NULL)", name="ck_eq_previous_coordinate_pair"),
        sa.CheckConstraint("previous_latitude IS NULL OR previous_latitude BETWEEN -90 AND 90", name="ck_eq_previous_latitude_range"),
        sa.CheckConstraint("previous_longitude IS NULL OR previous_longitude BETWEEN -180 AND 180", name="ck_eq_previous_longitude_range"),
        sa.ForeignKeyConstraint(["observation_id"], ["source_observations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("observation_id"),
    )
    op.create_index("ix_earthquake_observations_origin_time", "earthquake_observations", ["origin_time"])
    op.execute(sa.text("""
        INSERT INTO earthquake_observations
            (observation_id, origin_time, latitude, longitude, magnitude, depth_km,
             provider_status, updated_at)
        SELECT o.id, o.origin_time, o.latitude, o.longitude, o.magnitude, o.depth_km,
               CASE WHEN s.code = 'usgs' THEN o.raw_payload #>> '{properties,status}' ELSE NULL END,
               CURRENT_TIMESTAMP
        FROM source_observations AS o
        JOIN sources AS s ON s.id = o.source_id
    """))
    op.alter_column("earthquake_observations", "updated_at", server_default=None)

    # The old observation columns are the source for the one-time detail-table
    # backfill above, so remove them only after every legacy row has been copied.
    op.drop_column("source_observations", "title")
    op.drop_column("source_observations", "origin_time")
    op.drop_column("source_observations", "latitude")
    op.drop_column("source_observations", "longitude")
    op.drop_column("source_observations", "magnitude")
    op.drop_column("source_observations", "depth_km")

    # Preserve previous migration's event-ID evidence while also normalizing
    # candidate references when the referenced canonical events exist.
    op.rename_table("correlation_decisions", "event_correlations")
    op.drop_constraint("correlation_decisions_observation_id_key", "event_correlations", type_="unique")
    op.drop_constraint("correlation_decisions_observation_id_fkey", "event_correlations", type_="foreignkey")
    op.drop_constraint("correlation_decisions_selected_event_id_fkey", "event_correlations", type_="foreignkey")
    op.alter_column("event_correlations", "observation_id", nullable=False)
    op.add_column("event_correlations", sa.Column("method", sa.String(length=80), nullable=False, server_default="legacy_unknown"))
    op.add_column("event_correlations", sa.Column("method_version", sa.String(length=40), nullable=False, server_default="legacy"))
    op.alter_column("event_correlations", "method", server_default=None)
    op.alter_column("event_correlations", "method_version", server_default=None)
    op.create_foreign_key(
        "fk_event_correlations_observation", "event_correlations", "source_observations",
        ["observation_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_event_correlations_selected_event", "event_correlations", "events",
        ["selected_event_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index("ix_event_correlations_observation_id", "event_correlations", ["observation_id"])

    op.create_table(
        "event_correlation_candidates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("correlation_id", sa.UUID(), nullable=False),
        sa.Column("candidate_event_id", sa.UUID(), nullable=False),
        sa.Column("candidate_observation_id", sa.UUID(), nullable=True),
        sa.Column("distance_km", sa.Float(), nullable=True),
        sa.Column("time_delta_seconds", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["correlation_id"], ["event_correlations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["candidate_event_id"], ["events.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["candidate_observation_id"], ["source_observations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_event_correlation_candidates_correlation_id", "event_correlation_candidates", ["correlation_id"])
    op.create_index("ix_event_correlation_candidates_candidate_event_id", "event_correlation_candidates", ["candidate_event_id"])
    op.create_index("ix_event_correlation_candidates_candidate_observation_id", "event_correlation_candidates", ["candidate_observation_id"])
    op.execute(sa.text("""
        INSERT INTO event_correlation_candidates
            (id, correlation_id, candidate_event_id, distance_km, time_delta_seconds, confidence, selected)
        SELECT gen_random_uuid(), c.id, candidate.value::uuid, c.distance_km,
               c.time_delta_seconds, c.confidence,
               (candidate.value::uuid = c.selected_event_id)
        FROM event_correlations AS c
        CROSS JOIN LATERAL json_array_elements_text(c.candidate_event_ids) AS candidate(value)
        JOIN events AS e ON e.id = candidate.value::uuid
        WHERE candidate.value ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    """))

    op.create_table(
        "episode_observations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("episode_id", sa.UUID(), nullable=False),
        sa.Column("observation_id", sa.UUID(), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["episode_id"], ["event_episodes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["observation_id"], ["source_observations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("episode_id", "observation_id", name="uq_episode_observation"),
    )
    op.alter_column("episode_observations", "linked_at", server_default=None)


def downgrade() -> None:
    raise RuntimeError(
        "This reconciliation migration is forward-only; downgrade would discard current/previous values, "
        "episode links, and normalized correlation evidence. Restore from a verified backup instead."
    )
