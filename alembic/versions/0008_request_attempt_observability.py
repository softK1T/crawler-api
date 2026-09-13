"""Persist per-attempt crawler and proxy observability.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-13

One request_log row now represents ONE transport attempt (one retry-loop
iteration), not one logical job.  Adds attempt counters, outcome, proxy
metadata snapshot, a DEFAULT partition so inserts never fail after the last
yearly partition, and a proxy_usage_daily aggregate view.

Outcome stays a VARCHAR + CHECK constraint (no PG enum) so adding outcomes
does not require enum surgery.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels = None
depends_on = None

# Allowed outcome values (application level).  'unknown' is the server
# default for legacy rows predating this migration.
OUTCOME_LITERALS = (
    "'success', 'blocked', 'fetch_error', 'network_error', 'proxy_pool_empty', "
    "'proxy_pool_exhausted', 'cancelled', 'internal_error', 'unknown'"
)


def upgrade() -> None:
    # ── Attempt-level columns (one row per transport attempt) ──────────────
    op.add_column("request_log", sa.Column("job_id", sa.String(length=64), nullable=True))
    op.add_column(
        "request_log",
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.add_column(
        "request_log",
        sa.Column(
            "tier_attempt_number", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
    )
    op.add_column(
        "request_log",
        sa.Column("escalation_tier", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "request_log",
        sa.Column(
            "outcome", sa.String(length=32), nullable=False, server_default=sa.text("'unknown'")
        ),
    )
    op.add_column(
        "request_log", sa.Column("block_reason", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "request_log", sa.Column("error_type", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "request_log", sa.Column("proxy_provider", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "request_log", sa.Column("proxy_type", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "request_log", sa.Column("proxy_country", sa.String(length=2), nullable=True)
    )
    op.add_column(
        "request_log", sa.Column("proxy_city", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "request_log",
        sa.Column("proxy_pool_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "request_log", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )

    # ── Outcome CHECK constraint (VARCHAR, not a PG enum) ──────────────────
    op.create_check_constraint(
        "ck_request_log_outcome",
        "request_log",
        f"outcome IN ({OUTCOME_LITERALS})",
    )

    # ── Query indexes on the partitioned parent ────────────────────────────
    # Newly created partitions (including the DEFAULT one below) inherit these.
    op.create_index(
        "ix_reqlog_job_attempt", "request_log", ["job_id", "attempt_number"]
    )
    op.create_index("ix_reqlog_proxy_time", "request_log", ["proxy_id", "requested_at"])
    op.create_index("ix_reqlog_outcome_time", "request_log", ["outcome", "requested_at"])

    # ── DEFAULT partition: inserts never fail after the last yearly one ────
    # Re-attach a previously detached table (left behind by a downgrade)
    # when present; create fresh when absent; no-op when already attached.
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('request_log_default') IS NULL THEN
                CREATE TABLE request_log_default PARTITION OF request_log DEFAULT;
            ELSIF NOT EXISTS (
                SELECT 1 FROM pg_inherits
                WHERE inhrelid = 'request_log_default'::regclass
                  AND inhparent = 'request_log'::regclass
            ) THEN
                ALTER TABLE request_log ATTACH PARTITION request_log_default DEFAULT;
            END IF;
        END $$;
        """
    )

    # ── proxy_usage_daily aggregate view ───────────────────────────────────
    op.execute(
        """
        CREATE VIEW proxy_usage_daily AS
        SELECT
            (requested_at AT TIME ZONE 'UTC')::date AS usage_date,
            proxy_id,
            proxy_provider,
            proxy_type,
            proxy_country,
            domain,
            engine,
            count(*) AS attempts,
            count(*) FILTER (WHERE outcome = 'success') AS successes,
            count(*) FILTER (WHERE blocked) AS blocked_attempts,
            count(*) FILTER (
                WHERE outcome NOT IN ('success', 'blocked')
            ) AS failed_attempts,
            coalesce(sum(bytes_received), 0) AS bytes_received,
            round(avg(duration_ms), 2) AS average_duration_ms
        FROM request_log
        WHERE proxy_id IS NOT NULL
        GROUP BY 1, 2, 3, 4, 5, 6, 7
        """
    )


def downgrade() -> None:
    # Drop view first — it depends on the columns being removed.
    op.execute("DROP VIEW IF EXISTS proxy_usage_daily")
    # Drop the partitioned indexes BEFORE detaching: their per-partition
    # children (including the DEFAULT partition's) go with them, so a later
    # re-upgrade can recreate the same index names without collisions.
    op.drop_index("ix_reqlog_outcome_time", table_name="request_log")
    op.drop_index("ix_reqlog_proxy_time", table_name="request_log")
    op.drop_index("ix_reqlog_job_attempt", table_name="request_log")
    # DETACH, never DROP: every row with requested_at outside the yearly
    # partitions lives in the DEFAULT partition (all traffic after 2027),
    # and a downgrade must not erase audit history.  The table survives as a
    # standalone relation — a re-upgrade re-attaches it.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_inherits
                WHERE inhrelid = 'request_log_default'::regclass
                  AND inhparent = 'request_log'::regclass
            ) THEN
                ALTER TABLE request_log DETACH PARTITION request_log_default;
            END IF;
        END $$;
        """
    )
    op.drop_constraint("ck_request_log_outcome", "request_log", type_="check")
    op.drop_column("request_log", "completed_at")
    op.drop_column("request_log", "proxy_pool_id")
    op.drop_column("request_log", "proxy_city")
    op.drop_column("request_log", "proxy_country")
    op.drop_column("request_log", "proxy_type")
    op.drop_column("request_log", "proxy_provider")
    op.drop_column("request_log", "error_type")
    op.drop_column("request_log", "block_reason")
    op.drop_column("request_log", "outcome")
    op.drop_column("request_log", "escalation_tier")
    op.drop_column("request_log", "tier_attempt_number")
    op.drop_column("request_log", "attempt_number")
    op.drop_column("request_log", "job_id")
