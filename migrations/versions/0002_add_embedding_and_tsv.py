"""add embedding + tsv columns and indexes for hybrid retrieval

Revision ID: 0002_add_embedding_and_tsv
Revises: 0001_init
Create Date: 2026-05-07
"""

from __future__ import annotations

from alembic import op

revision: str = "0002_add_embedding_and_tsv"
down_revision: str | None = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1024-dim vector for Voyage voyage-4-lite output. Existing rows get
    # NULL — graceful: hybrid retrieval (M14b) will treat NULL as a missing
    # vector signal and rank via BM25 alone for those rows.
    op.execute("ALTER TABLE memories ADD COLUMN embedding vector(1024)")

    # tsvector covers key + value with key-component splitting so canonical
    # keys like `pet:dog:name` produce searchable tokens.
    op.execute(
        """
        ALTER TABLE memories ADD COLUMN tsv tsvector
        GENERATED ALWAYS AS (
            to_tsvector(
                'english',
                coalesce(replace(key, ':', ' '), '') || ' ' || coalesce(value, '')
            )
        ) STORED
        """
    )

    # HNSW for cosine — Voyage outputs are unit-normalised so cosine is the
    # right operator class. Defaults (m=16, ef_construction=64) are fine for
    # our scale; tuneable later via index rebuild.
    op.execute(
        "CREATE INDEX memories_embedding_idx "
        "ON memories USING hnsw (embedding vector_cosine_ops)"
    )

    op.execute("CREATE INDEX memories_tsv_idx ON memories USING GIN (tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS memories_tsv_idx")
    op.execute("DROP INDEX IF EXISTS memories_embedding_idx")
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS tsv")
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS embedding")
