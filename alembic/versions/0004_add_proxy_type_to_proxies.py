"""add proxy_type to proxies

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-03 23:00:00.000000

proxy_type distinguishes residential from datacenter proxies.
Existing rows default to 'datacenter' — backward-compatible.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Enum type for proxy_type
proxy_type_enum = sa.Enum("residential", "datacenter", name="proxy_type_enum")


def upgrade() -> None:
    proxy_type_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "proxies",
        sa.Column(
            "proxy_type",
            proxy_type_enum,
            nullable=False,
            server_default="datacenter",
        ),
    )


def downgrade() -> None:
    op.drop_column("proxies", "proxy_type")
    proxy_type_enum.drop(op.get_bind(), checkfirst=True)
