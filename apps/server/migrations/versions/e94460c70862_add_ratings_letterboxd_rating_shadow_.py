"""add ratings.letterboxd_rating shadow column

Revision ID: e94460c70862
Revises: cf139bc6fb00
Create Date: 2026-07-04 00:15:40.697203

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e94460c70862'
down_revision: Union[str, Sequence[str], None] = 'cf139bc6fb00'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("ratings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("letterboxd_rating", sa.Float(), nullable=True))
        batch_op.create_check_constraint(
            "ck_ratings_letterboxd_rating_range",
            "letterboxd_rating IS NULL OR (letterboxd_rating >= 0.5 AND letterboxd_rating <= 5.0)",
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("ratings", schema=None) as batch_op:
        batch_op.drop_constraint("ck_ratings_letterboxd_rating_range", type_="check")
        batch_op.drop_column("letterboxd_rating")
