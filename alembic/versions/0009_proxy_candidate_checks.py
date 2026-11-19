"""Opt-in public proxy candidate checks.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE proxy_type_enum ADD VALUE IF NOT EXISTS 'unknown'")
    for name in ("check_managed", "check_auto_activate"):
        op.add_column(
            "proxies", sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.add_column(
        "proxies",
        sa.Column("check_status", sa.String(16), nullable=False, server_default="unmanaged"),
    )
    for name, length in (("exit_ip", 45), ("country_source", 64), ("check_error", 64)):
        op.add_column("proxies", sa.Column(name, sa.String(length), nullable=True))
    for name in ("check_last_at", "check_due_at"):
        op.add_column("proxies", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_proxy_candidate_due", "proxies", ["check_managed", "check_due_at"])


def downgrade() -> None:
    op.drop_index("ix_proxy_candidate_due", table_name="proxies")
    for name in (
        "check_due_at", "check_last_at", "check_error", "country_source",
        "exit_ip", "check_status", "check_auto_activate", "check_managed",
    ):
        op.drop_column("proxies", name)
    # Keep the enum value: rows may still use unknown. Do not relabel or delete them.
