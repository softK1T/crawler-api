"""add seq column to proxy_events for deterministic ordering

Revision ID: d96e3566e6ba
Revises: 0006
Create Date: 2026-08-09 16:33:01.769889
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd96e3566e6ba'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "proxy_events",
        sa.Column("seq", sa.BigInteger(), sa.Identity(), nullable=False),
    )
    op.create_unique_constraint("uq_proxy_events_seq", "proxy_events", ["seq"])


def downgrade() -> None:
    op.drop_constraint("uq_proxy_events_seq", "proxy_events", type_="unique")
    op.drop_column("proxy_events", "seq")

