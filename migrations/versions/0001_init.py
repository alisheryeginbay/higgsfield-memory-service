"""init: turns + memories + pgvector extension

Revision ID: 0001_init
Revises:
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op

revision: str = "0001_init"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Available even though no vector columns exist yet — later migrations
    # add them, and this keeps the asyncpg pgvector codec init happy.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    op.execute(
        """
        CREATE TABLE turns (
            id              uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
            session_id      text        NOT NULL,
            user_id         text        NULL,
            messages        jsonb       NOT NULL,
            ts              timestamptz NOT NULL,
            metadata        jsonb       NOT NULL DEFAULT '{}'::jsonb,
            created_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX turns_session_idx ON turns (session_id)")
    op.execute("CREATE INDEX turns_user_ts_idx ON turns (user_id, ts DESC)")

    op.execute(
        """
        CREATE TABLE memories (
            id              uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id         text        NOT NULL,
            type            text        NOT NULL
                              CHECK (type IN ('fact','preference','opinion','event')),
            key             text        NOT NULL,
            value           text        NOT NULL,
            confidence      double precision NOT NULL DEFAULT 0.5,
            source_session  text        NOT NULL,
            source_turn     uuid        NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            supersedes      uuid        NULL REFERENCES memories(id) ON DELETE SET NULL,
            active          boolean     NOT NULL DEFAULT TRUE
        )
        """
    )
    op.execute("CREATE INDEX memories_user_active_idx ON memories (user_id, active)")
    op.execute("CREATE INDEX memories_user_key_idx ON memories (user_id, key)")
    op.execute("CREATE INDEX memories_source_session_idx ON memories (source_session)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memories CASCADE")
    op.execute("DROP TABLE IF EXISTS turns CASCADE")
    # Leave extensions in place — dropping them can break other DB users.
