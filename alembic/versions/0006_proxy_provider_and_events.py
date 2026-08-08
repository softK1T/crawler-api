"""add proxy provider identity and durable proxy events

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-06 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "proxies",
        sa.Column(
            "provider",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'webshare'"),
        ),
    )
    op.add_column(
        "proxies",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_unique_constraint("uq_proxy_provider_url", "proxies", ["provider", "url"])

    op.create_table(
        "proxy_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "proxy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("proxies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_proxy_events_proxy_time", "proxy_events", ["proxy_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_proxy_events_proxy_time", table_name="proxy_events")
    op.drop_table("proxy_events")
    op.drop_constraint("uq_proxy_provider_url", "proxies", type_="unique")
    op.drop_column("proxies", "is_active")
    op.drop_column("proxies", "provider")
