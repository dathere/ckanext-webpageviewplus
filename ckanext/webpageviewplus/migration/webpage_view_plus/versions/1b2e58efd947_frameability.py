"""frameability

The last frameability check of each webpage_view_plus view's page, taken
when the view is saved or by `ckan webpageviewplus check-views`. Rendering
reads it, so visitors never make CKAN fetch anything.

Revision ID: 1b2e58efd947
Revises: 929956801471
Create Date: 2026-10-28 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1b2e58efd947'
down_revision = '929956801471'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "webpage_view_plus_frameability",
        sa.Column(
            "view_id", sa.UnicodeText,
            sa.ForeignKey("resource_view.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("url", sa.UnicodeText, nullable=False),
        sa.Column("verdict", sa.UnicodeText, nullable=False),
        sa.Column("reason", sa.UnicodeText, nullable=False, server_default=""),
        sa.Column("problem", sa.UnicodeText, nullable=False, server_default=""),
        sa.Column("status", sa.Integer),
        sa.Column("checked_at", sa.DateTime, nullable=False),
    )


def downgrade():
    op.drop_table("webpage_view_plus_frameability")
