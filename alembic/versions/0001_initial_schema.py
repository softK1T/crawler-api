"""initial schema: tenants, applications, api_keys, proxies, domain_policy, request_log, usage_counter, warc_index

Revision ID: 0001
Revises:
Create Date: 2026-07-27
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # tenants
    # ------------------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), unique=True, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_tenants_is_active", "tenants", ["is_active"])

    # ------------------------------------------------------------------
    # applications
    # ------------------------------------------------------------------
    op.create_table(
        "applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_application_tenant_name"),
    )
    op.create_index("ix_applications_tenant_id", "applications", ["tenant_id"])
    op.create_index("ix_applications_is_active", "applications", ["is_active"])

    # ------------------------------------------------------------------
    # api_keys
    # ------------------------------------------------------------------
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("application_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prefix", sa.String(8), unique=True, nullable=False),
        sa.Column("hashed_key", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_prefix", "api_keys", ["prefix"])
    op.create_index("ix_api_keys_application_id", "api_keys", ["application_id"])
    op.create_index("ix_api_keys_is_active_expires_at", "api_keys", ["is_active", "expires_at"])

    # ------------------------------------------------------------------
    # proxy_pools
    # ------------------------------------------------------------------
    op.create_table(
        "proxy_pools",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), unique=True, nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    # ------------------------------------------------------------------
    # proxies
    # ------------------------------------------------------------------
    op.create_table(
        "proxies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("pool_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("proxy_pools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("health_score", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column("consecutive_failures", sa.BigInteger(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_requests", sa.BigInteger(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("total_errors", sa.BigInteger(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_proxy_pool_health", "proxies", ["pool_id", "health_score"])
    op.create_index("ix_proxy_cooldown", "proxies", ["cooldown_until"])
    op.create_index("ix_proxy_country", "proxies", ["country"])

    # ------------------------------------------------------------------
    # domain_policies
    # ------------------------------------------------------------------
    op.create_table(
        "domain_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("domain", sa.String(255), unique=True, nullable=False),
        sa.Column("proxy_pool_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("proxy_pools.id", ondelete="SET NULL"), nullable=True),
        sa.Column("engine", sa.String(16), nullable=False, server_default=sa.text("'httpx'")),
        sa.Column("rate_limit_rps", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column("min_delay_ms", sa.Integer(), nullable=False, server_default=sa.text("500")),
        sa.Column("max_delay_ms", sa.Integer(), nullable=False, server_default=sa.text("2000")),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("respect_robots", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("header_profile", postgresql.JSONB(), nullable=True),
        sa.Column("sticky_session", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_domain_policies_domain", "domain_policies", ["domain"])
    op.create_index("ix_domain_policies_is_active", "domain_policies", ["is_active"])

    # ------------------------------------------------------------------
    # request_log (PARTITIONED — manual DDL required)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE request_log (
            id UUID NOT NULL,
            api_key_id UUID REFERENCES api_keys(id) ON DELETE SET NULL,
            application_id UUID REFERENCES applications(id) ON DELETE SET NULL,
            domain VARCHAR(255) NOT NULL,
            url TEXT NOT NULL,
            method VARCHAR(8) NOT NULL DEFAULT 'GET',
            status_code INTEGER,
            proxy_id UUID REFERENCES proxies(id) ON DELETE SET NULL,
            engine VARCHAR(16) NOT NULL,
            duration_ms INTEGER,
            bytes_received BIGINT,
            blocked BOOLEAN NOT NULL DEFAULT FALSE,
            error TEXT,
            trace_id VARCHAR(64),
            requested_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (id, requested_at)
        ) PARTITION BY RANGE (requested_at)
    """)
    op.execute("""
        CREATE TABLE request_log_y2026 PARTITION OF request_log
        FOR VALUES FROM ('2026-01-01') TO ('2027-01-01')
    """)
    op.execute("""
        CREATE TABLE request_log_y2027 PARTITION OF request_log
        FOR VALUES FROM ('2027-01-01') TO ('2028-01-01')
    """)
    op.create_index("ix_reqlog_app_time", "request_log", ["application_id", "requested_at"])
    op.create_index("ix_reqlog_domain_time", "request_log", ["domain", "requested_at"])
    op.create_index("ix_reqlog_apikey_time", "request_log", ["api_key_id", "requested_at"])

    # ------------------------------------------------------------------
    # usage_counters
    # ------------------------------------------------------------------
    op.create_table(
        "usage_counters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("application_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_month", sa.Date(), nullable=False),
        sa.Column("request_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("bytes_received", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost_eur_cents", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("application_id", "period_month", name="uq_usage_app_period"),
    )
    op.create_index("ix_usage_app_period", "usage_counters",
                    ["application_id", "period_month"])

    # ------------------------------------------------------------------
    # warc_index
    # ------------------------------------------------------------------
    op.create_table(
        "warc_index",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("request_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("warc_filename", sa.String(512), nullable=False),
        sa.Column("offset", sa.BigInteger(), nullable=False),
        sa.Column("length", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("is_revisit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("content_type", sa.String(255), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_warc_sha256", "warc_index", ["sha256"])
    op.create_index("ix_warc_url_time", "warc_index", ["url", "captured_at"])
    op.create_index("ix_warc_filename_offset", "warc_index", ["warc_filename", "offset"])
    op.create_index("ix_warc_captured_at", "warc_index", ["captured_at"])


def downgrade() -> None:
    op.drop_table("warc_index")
    op.drop_table("usage_counters")
    op.execute("DROP TABLE IF EXISTS request_log_y2026")
    op.execute("DROP TABLE IF EXISTS request_log_y2027")
    op.execute("DROP TABLE IF EXISTS request_log CASCADE")
    op.drop_table("domain_policies")
    op.drop_table("proxies")
    op.drop_table("proxy_pools")
    op.drop_table("api_keys")
    op.drop_table("applications")
    op.drop_table("tenants")
