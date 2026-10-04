"""Wardrobe, styling sessions, revision history and local developer access."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
    )
    op.create_table(
        "access_tokens",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_access_tokens_user_id", "access_tokens", ["user_id"])
    op.create_table(
        "clothing_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("colors", sa.JSON(), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("role IN ('top', 'bottom', 'shoes', 'one_piece', 'layer')"),
    )
    op.create_index("ix_clothing_items_owner_id", "clothing_items", ["owner_id"])
    op.create_table(
        "styling_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("selected_revision_id", sa.Uuid(), nullable=True),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("status IN ('active', 'selected')"),
    )
    op.create_index("ix_styling_sessions_owner_id", "styling_sessions", ["owner_id"])
    op.create_table(
        "outfit_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("styling_sessions.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("outfit_revisions.id"), nullable=True),
        sa.Column("item_ids", sa.JSON(), nullable=False),
        sa.Column("item_snapshot", sa.JSON(), nullable=False),
        sa.Column("change", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "number"),
    )
    op.create_index("ix_outfit_revisions_session_id", "outfit_revisions", ["session_id"])


def downgrade():
    op.drop_table("outfit_revisions")
    op.drop_table("styling_sessions")
    op.drop_table("clothing_items")
    op.drop_table("access_tokens")
    op.drop_table("users")
