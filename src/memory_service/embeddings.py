"""Voyage AI embeddings for memory storage + recall query encoding.

`voyage-4-lite` is 1024-dim with asymmetric `input_type` (separate document
and query encoders). Vectors are unit-normalised by Voyage, so downstream
cosine similarity equals dot product — `vector_cosine_ops` is the right
pgvector operator class.

Failures here never crash the request path. The caller (`/turns` for
documents, `/recall` for queries) wraps these calls so a Voyage hiccup
degrades to NULL embeddings (still BM25-retrievable in M14b) instead of
breaking ingestion.
"""

from __future__ import annotations

import logging
from typing import Protocol

import voyageai

log = logging.getLogger(__name__)


class EmbeddingError(Exception):
    """Voyage call failed or returned an unexpected shape."""


class Embedder(Protocol):
    async def embed_documents(
        self, texts: list[str]
    ) -> list[list[float] | None]: ...

    async def embed_query(self, text: str) -> list[float] | None: ...


class NoopEmbedder:
    """Used when ``VOYAGE_API_KEY`` is unset. Always yields ``None``.

    Memories still persist (with NULL embedding); recall falls back to
    keyword search alone in M14b.
    """

    async def embed_documents(
        self, texts: list[str]
    ) -> list[list[float] | None]:
        return [None] * len(texts)

    async def embed_query(self, text: str) -> list[float] | None:
        return None


class VoyageEmbedder:
    def __init__(
        self,
        client: voyageai.AsyncClient,
        model: str = "voyage-4-lite",
        output_dimension: int = 1024,
    ) -> None:
        self.client = client
        self.model = model
        self.output_dimension = output_dimension

    async def embed_documents(
        self, texts: list[str]
    ) -> list[list[float] | None]:
        if not texts:
            return []
        try:
            resp = await self.client.embed(
                texts=texts,
                model=self.model,
                input_type="document",
                output_dimension=self.output_dimension,
            )
        except Exception as e:
            raise EmbeddingError(f"voyage embed_documents failed: {e}") from e

        embs = list(resp.embeddings)
        if len(embs) != len(texts):
            raise EmbeddingError(
                f"shape mismatch: {len(embs)} embeddings for {len(texts)} texts"
            )
        for i, e in enumerate(embs):
            if len(e) != self.output_dimension:
                raise EmbeddingError(
                    f"text[{i}]: got {len(e)}-dim, expected {self.output_dimension}"
                )
        return embs

    async def embed_query(self, text: str) -> list[float] | None:
        if not text.strip():
            return None
        try:
            resp = await self.client.embed(
                texts=[text],
                model=self.model,
                input_type="query",
                output_dimension=self.output_dimension,
            )
        except Exception as e:
            raise EmbeddingError(f"voyage embed_query failed: {e}") from e

        emb = list(resp.embeddings[0])
        if len(emb) != self.output_dimension:
            raise EmbeddingError(
                f"query embedding: got {len(emb)}-dim, expected {self.output_dimension}"
            )
        return emb
