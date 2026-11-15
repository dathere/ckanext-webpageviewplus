"""screenshots

A screenshot per webpage_view_plus view whose third-party page refuses to be
framed, taken in the background by the configured backend and shown on the
view's card (and in the resource list when nothing else is). A failed first
attempt leaves a row with no image, so the view form can say why.

Revision ID: 10a4e7ca2e38
Revises: 5d3f0c7a2b81
Create Date: 2026-11-15 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '10a4e7ca2e38'
down_revision = '5d3f0c7a2b81'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "webpage_view_plus_screenshots",
        sa.Column(
            "view_id", sa.UnicodeText,
            sa.ForeignKey("resource_view.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source", sa.UnicodeText, nullable=False),
        sa.Column("backend", sa.UnicodeText, nullable=False),
        sa.Column("final_host", sa.UnicodeText, nullable=False, server_default=""),
        sa.Column("image", sa.LargeBinary),
        sa.Column("thumb", sa.LargeBinary),
        sa.Column("digest", sa.UnicodeText),
        sa.Column("taken_at", sa.DateTime),
        sa.Column("attempted_at", sa.DateTime, nullable=False),
        sa.Column("last_error", sa.UnicodeText, nullable=False, server_default=""),
    )


def downgrade():
    op.drop_table("webpage_view_plus_screenshots")
