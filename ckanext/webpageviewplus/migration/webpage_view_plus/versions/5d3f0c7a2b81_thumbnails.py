"""thumbnails

A small stored image per webpage_view_plus view, shown in the dataset's
resource list by the webpage_view_plus_thumbnails plugin. Kept in the
database so it works without shared disk or local storage, and is backed up
with everything else.

Revision ID: 5d3f0c7a2b81
Revises: 1b2e58efd947
Create Date: 2026-10-31 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5d3f0c7a2b81'
down_revision = '1b2e58efd947'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "webpage_view_plus_thumbnails",
        sa.Column(
            "view_id", sa.UnicodeText,
            sa.ForeignKey("resource_view.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source", sa.UnicodeText, nullable=False),
        sa.Column("image", sa.LargeBinary, nullable=False),
        sa.Column("digest", sa.UnicodeText, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )


def downgrade():
    op.drop_table("webpage_view_plus_thumbnails")
