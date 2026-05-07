"""Unit tests for the extraction module — Anthropic transport mocked.

These run without the live service and without a real API key. They're
the iteration loop for prompt + parser changes. Live behaviour is
covered separately in test_extraction_live.py (skip-if-no-key).
"""

from __future__ import annotations

import anthropic
import httpx
import pytest
import respx

from memory_service.extraction import (
    ClaudeExtractor,
    ExtractionError,
    _render_transcript,
    _validate_memory,
)
from memory_service.schemas import Message


def _stub_response(memories: list[dict]) -> httpx.Response:
    """Build an Anthropic /v1/messages response with a tool_use block."""
    return httpx.Response(
        200,
        json={
            "id": "msg_test_01",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-4-5",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_test_01",
                    "name": "record_memories",
                    "input": {"memories": memories},
                }
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    )


def _client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key="test-key")


# --- helper coverage ------------------------------------------------------


def test_render_transcript_drops_system_and_blanks() -> None:
    out = _render_transcript(
        [
            Message(role="system", content="you are an agent"),
            Message(role="user", content="  "),
            Message(role="user", content="I work at Notion."),
            Message(role="assistant", content="Got it."),
        ]
    )
    assert out == "USER: I work at Notion.\n\nASSISTANT: Got it."


def test_validate_memory_rejects_bad_inputs() -> None:
    with pytest.raises(ExtractionError):
        _validate_memory("not a dict")
    with pytest.raises(ExtractionError):
        _validate_memory({"type": "fact", "key": "x", "value": "y"})  # missing confidence
    with pytest.raises(ExtractionError):
        _validate_memory(
            {"type": "weather", "key": "x", "value": "y", "confidence": 0.9}
        )
    with pytest.raises(ExtractionError):
        _validate_memory(
            {"type": "fact", "key": "", "value": "y", "confidence": 0.9}
        )
    with pytest.raises(ExtractionError):
        _validate_memory(
            {"type": "fact", "key": "x", "value": "y", "confidence": 1.5}
        )


# --- extractor end-to-end with mocked transport ---------------------------


@respx.mock
async def test_claude_extractor_returns_validated_memories() -> None:
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_stub_response(
            [
                {"type": "fact", "key": "employer", "value": "Notion", "confidence": 0.95},
                {"type": "fact", "key": "city", "value": "Berlin", "confidence": 0.9},
            ]
        )
    )
    extractor = ClaudeExtractor(_client(), "claude-haiku-4-5", confidence_floor=0.4)
    out = await extractor.extract([Message(role="user", content="I'm at Notion in Berlin.")])
    assert [m["key"] for m in out] == ["employer", "city"]
    assert all(m["confidence"] >= 0.4 for m in out)


@respx.mock
async def test_claude_extractor_filters_below_confidence_floor() -> None:
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_stub_response(
            [
                {"type": "fact", "key": "employer", "value": "Notion", "confidence": 0.95},
                {"type": "opinion", "key": "vibes", "value": "Notion is fine", "confidence": 0.3},
            ]
        )
    )
    extractor = ClaudeExtractor(_client(), "claude-haiku-4-5", confidence_floor=0.5)
    out = await extractor.extract([Message(role="user", content="I'm at Notion.")])
    assert len(out) == 1
    assert out[0]["key"] == "employer"


@respx.mock
async def test_claude_extractor_skips_invalid_items_keeps_valid() -> None:
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_stub_response(
            [
                {"type": "fact", "key": "employer", "value": "Notion", "confidence": 0.9},
                {"type": "fact", "key": "city"},  # malformed — dropped
                "totally not a dict",  # malformed — dropped
            ]
        )
    )
    extractor = ClaudeExtractor(_client(), "claude-haiku-4-5", confidence_floor=0.4)
    out = await extractor.extract([Message(role="user", content="I'm at Notion.")])
    assert len(out) == 1
    assert out[0]["key"] == "employer"


@respx.mock
async def test_claude_extractor_empty_messages_skips_call() -> None:
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_stub_response([])
    )
    extractor = ClaudeExtractor(_client(), "claude-haiku-4-5")
    out = await extractor.extract([Message(role="system", content="meta")])
    assert out == []
    assert route.call_count == 0  # nothing to extract → no API call


@respx.mock
async def test_claude_extractor_raises_on_api_error() -> None:
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(500, json={"error": {"type": "api_error", "message": "boom"}})
    )
    extractor = ClaudeExtractor(_client(), "claude-haiku-4-5")
    with pytest.raises(ExtractionError):
        await extractor.extract([Message(role="user", content="hello")])


@respx.mock
async def test_claude_extractor_raises_on_no_tool_use_block() -> None:
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-4-5",
                "content": [{"type": "text", "text": "I refuse"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 5, "output_tokens": 2},
            },
        )
    )
    extractor = ClaudeExtractor(_client(), "claude-haiku-4-5")
    with pytest.raises(ExtractionError):
        await extractor.extract([Message(role="user", content="hi")])
