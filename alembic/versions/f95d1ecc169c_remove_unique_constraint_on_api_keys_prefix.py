"""remove unique constraint on api_keys.prefix

Revision ID: f95d1ecc169c
Revises: 0001
Create Date: 2026-07-28 22:02:00.000000

Prefix-based key lookup handles multiple keys per prefix in both the
in-memory registry (setdefault→list) and the DB-backed resolver.
The unique constraint was a design error (ADR-013).
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f95d1ecc169c"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("api_keys_prefix_key", "api_keys", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint("api_keys_prefix_key", "api_keys", ["prefix"])
