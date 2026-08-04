"""Add anti-bot escalation fields to domain_policies and proxy_type enum values.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new enum values to proxy_type_enum.
    # NOTE: Postgres cannot remove enum values — downgrade is a documented no-op for these.
    op.execute("ALTER TYPE proxy_type_enum ADD VALUE IF NOT EXISTS 'mobile'")
    op.execute("ALTER TYPE proxy_type_enum ADD VALUE IF NOT EXISTS 'isp'")

    op.add_column(
        "domain_policies",
        sa.Column("antibot_type", sa.String(32), nullable=True),
    )
    op.add_column(
        "domain_policies",
        sa.Column(
            "proxy_type",
            sa.String(16),
            nullable=True,
        ),
    )
    op.add_column(
        "domain_policies",
        sa.Column(
            "escalation_tier",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "domain_policies",
        sa.Column(
            "tier_locked",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "domain_policies",
        sa.Column(
            "last_success_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "domain_policies",
        sa.Column("last_block_reason", sa.String(32), nullable=True),
    )
    op.add_column(
        "domain_policies",
        sa.Column(
            "consecutive_blocks",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "domain_policies",
        sa.Column(
            "max_escalation_attempts",
            sa.Integer(),
            nullable=False,
            server_default="12",
        ),
    )


def downgrade() -> None:
    op.drop_column("domain_policies", "max_escalation_attempts")
    op.drop_column("domain_policies", "consecutive_blocks")
    op.drop_column("domain_policies", "last_block_reason")
    op.drop_column("domain_policies", "last_success_at")
    op.drop_column("domain_policies", "tier_locked")
    op.drop_column("domain_policies", "escalation_tier")
    op.drop_column("domain_policies", "proxy_type")
    op.drop_column("domain_policies", "antibot_type")
    # NOTE: proxy_type_enum values 'mobile' and 'isp' are NOT removed on downgrade.
    # Postgres does not support DROP VALUE for enums. If you need a clean rollback,
    # drop and recreate the enum type manually after removing all references.
