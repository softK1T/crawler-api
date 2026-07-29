"""restore unique constraint on api_keys.prefix

Revision ID: a7c55bf575f3
Revises: f95d1ecc169c
Create Date: 2026-07-29 12:00:00.000000

Prefixes are now distinctive (include random chars) so the unique constraint
is both correct and necessary for O(1) key lookup.
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c55bf575f3"
down_revision: Union[str, None] = "f95d1ecc169c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint("api_keys_prefix_key", "api_keys", ["prefix"])


def downgrade() -> None:
    op.drop_constraint("api_keys_prefix_key", "api_keys", type_="unique")
