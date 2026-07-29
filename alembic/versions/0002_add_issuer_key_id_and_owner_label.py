"""add issuer_key_id to api_keys and owner_label to applications

Revision ID: 0002
Revises: a7c55bf575f3
Create Date: 2026-07-29 14:00:00.000000

issuer_key_id records which operator key minted the row (nullable, no FK
to avoid a self-referential constraint). owner_label gives the operator a
human-readable label for the key's owner at issuance time.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "a7c55bf575f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column(
            "issuer_key_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "applications",
        sa.Column(
            "owner_label",
            sa.String(255),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "issuer_key_id")
    op.drop_column("applications", "owner_label")
