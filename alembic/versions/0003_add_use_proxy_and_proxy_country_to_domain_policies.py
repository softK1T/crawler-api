"""add use_proxy and proxy_country to domain_policies

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-03 10:00:00.000000

use_proxy controls whether a proxy is selected for this domain (default True).
proxy_country is an optional ISO 3166-1 alpha-2 code to pin proxy geography.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "domain_policies",
        sa.Column("use_proxy", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "domain_policies",
        sa.Column("proxy_country", sa.String(2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("domain_policies", "proxy_country")
    op.drop_column("domain_policies", "use_proxy")
