"""add city to proxies

Revision ID: 0007
Revises: d96e3566e6ba
Create Date: 2026-09-09 16:22:00
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "d96e3566e6ba"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("proxies", sa.Column("city", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("proxies", "city")
