"""Unit tests for the Voyage embedder.

Mocks the SDK's `client.embed` directly via `unittest.mock.AsyncMock` —
voyageai uses aiohttp internally, so respx (which targets httpx) doesn't
help. Mocking at the SDK boundary tests the wrapping logic (validation,
shape checks, error wrapping) without needing the network.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from memory_service.embeddings import (
    EmbeddingError,
    NoopEmbedder,
    VoyageEmbedder,
)


def _resp(embeddings: list[list[float]], total_tokens: int = 0) -> SimpleNamespace:
    """Mimic voyageai.EmbeddingsObject's surface."""
    return SimpleNamespace(embeddings=embeddings, total_tokens=total_tokens)


def _mk_extractor(client: AsyncMock) -> VoyageEmbedder:
    return VoyageEmbedder(client=client, model="voyage-4-lite", output_dimension=4)


# --- NoopEmbedder ---------------------------------------------------------


async def test_noop_embed_documents_returns_none_per_text() -> None:
    e = NoopEmbedder()
    out = await e.embed_documents(["a", "b", "c"])
    assert out == [None, None, None]


async def test_noop_embed_documents_empty() -> None:
    e = NoopEmbedder()
    assert await e.embed_documents([]) == []


async def test_noop_embed_query_returns_none() -> None:
    e = NoopEmbedder()
    assert await e.embed_query("anything") is None


# --- VoyageEmbedder happy paths -------------------------------------------


async def test_embed_documents_returns_validated_batch() -> None:
    client = AsyncMock()
    client.embed.return_value = _resp([[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]])
    embedder = _mk_extractor(client)

    out = await embedder.embed_documents(["t1", "t2"])
    assert out == [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]]
    client.embed.assert_awaited_once()
    kwargs = client.embed.await_args.kwargs
    assert kwargs["texts"] == ["t1", "t2"]
    assert kwargs["model"] == "voyage-4-lite"
    assert kwargs["input_type"] == "document"
    assert kwargs["output_dimension"] == 4


async def test_embed_documents_empty_skips_api_call() -> None:
    client = AsyncMock()
    embedder = _mk_extractor(client)

    out = await embedder.embed_documents([])
    assert out == []
    client.embed.assert_not_awaited()


async def test_embed_query_returns_single_vector() -> None:
    client = AsyncMock()
    client.embed.return_value = _resp([[1.0, 0.0, 0.0, 0.0]])
    embedder = _mk_extractor(client)

    out = await embedder.embed_query("where do they live")
    assert out == [1.0, 0.0, 0.0, 0.0]
    kwargs = client.embed.await_args.kwargs
    assert kwargs["input_type"] == "query"


async def test_embed_query_blank_string_skips_api_call() -> None:
    client = AsyncMock()
    embedder = _mk_extractor(client)

    out = await embedder.embed_query("   ")
    assert out is None
    client.embed.assert_not_awaited()


# --- VoyageEmbedder error paths -------------------------------------------


async def test_embed_documents_raises_on_sdk_error() -> None:
    client = AsyncMock()
    client.embed.side_effect = RuntimeError("boom")
    embedder = _mk_extractor(client)

    with pytest.raises(EmbeddingError):
        await embedder.embed_documents(["t1"])


async def test_embed_documents_length_mismatch_raises() -> None:
    client = AsyncMock()
    # 2 texts in, 1 embedding out
    client.embed.return_value = _resp([[0.1, 0.2, 0.3, 0.4]])
    embedder = _mk_extractor(client)

    with pytest.raises(EmbeddingError):
        await embedder.embed_documents(["t1", "t2"])


async def test_embed_documents_dim_mismatch_raises() -> None:
    client = AsyncMock()
    # output_dimension=4 but vector has 3 entries
    client.embed.return_value = _resp([[0.1, 0.2, 0.3]])
    embedder = _mk_extractor(client)

    with pytest.raises(EmbeddingError):
        await embedder.embed_documents(["t1"])


async def test_embed_query_dim_mismatch_raises() -> None:
    client = AsyncMock()
    client.embed.return_value = _resp([[0.1, 0.2]])
    embedder = _mk_extractor(client)

    with pytest.raises(EmbeddingError):
        await embedder.embed_query("hello")


async def test_embed_query_raises_on_sdk_error() -> None:
    client = AsyncMock()
    client.embed.side_effect = ValueError("bad input")
    embedder = _mk_extractor(client)

    with pytest.raises(EmbeddingError):
        await embedder.embed_query("hello")
