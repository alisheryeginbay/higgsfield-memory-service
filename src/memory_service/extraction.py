"""Turn → structured memory extraction via Claude tool-use.

The extractor is invoked synchronously from the `/turns` handler. It
receives the raw messages of a single turn, returns a list of typed
memories, and a separate helper persists those rows into the `memories`
table.

Failures here never block the turn from being persisted — that's a
deliberate design call (the turn is user data; extraction is derived
enrichment, and it should degrade gracefully when the LLM hiccups or no
API key is configured).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal, Protocol, TypedDict

import anthropic
import asyncpg

from memory_service.schemas import Message

log = logging.getLogger(__name__)


# --- types ---------------------------------------------------------------

MemoryType = Literal["fact", "preference", "opinion", "event"]


class ExtractedMemory(TypedDict):
    type: MemoryType
    key: str
    value: str
    confidence: float


class ExtractionError(Exception):
    """Anything that prevented us from getting a clean memory list back."""


# --- prompt + tool schema ------------------------------------------------

SYSTEM_PROMPT = """\
You are an extractor that pulls structured atomic memories about the USER
(not the assistant) from one conversation turn.

Categories:
- fact: stable, verifiable user info (employer, city, family, pets, demographics).
- preference: lasting like / dislike or stated tendency ("I prefer X", "I always Y").
- opinion: subjective view that may evolve ("I think X is great").
- event: specific transient occurrence ("starting new job Monday", "moving next month").

Rules:
- Skip the assistant's words. Skip generic chitchat ("hi", "thanks").
- Use canonical snake_case keys when obvious: employer, role, city, country,
  language_preference, pet:dog:name, pet:dog:breed, ide_preference, etc.
  Otherwise pick a short, stable, snake_case key.
- The `value` should be a concise human-readable assertion ("Notion", "Berlin",
  "Python for scripts").
- Confidence:
    * 0.9-1.0 — explicit unambiguous statement by the user.
    * 0.6-0.8 — clear implication ("walking Biscuit" → has a dog named Biscuit).
    * < 0.6 — guess; these will be filtered out, so prefer to omit.
- A correction inside the same turn ("I love TS — actually I prefer Python for
  scripts") should produce ONE memory reflecting the corrected (final)
  preference, not both.
- If nothing is worth extracting, call record_memories with an empty list.

Always return through the record_memories tool. Do not produce free-form text.
"""

TOOL_SCHEMA: dict[str, Any] = {
    "name": "record_memories",
    "description": "Record atomic memories about the user from this conversation turn.",
    "input_schema": {
        "type": "object",
        "properties": {
            "memories": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["fact", "preference", "opinion", "event"],
                        },
                        "key": {
                            "type": "string",
                            "description": "Short stable snake_case identifier.",
                        },
                        "value": {
                            "type": "string",
                            "description": "Concise human-readable assertion.",
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                    },
                    "required": ["type", "key", "value", "confidence"],
                },
            }
        },
        "required": ["memories"],
    },
}


# --- helpers -------------------------------------------------------------


def _render_transcript(messages: list[Message]) -> str:
    """Flatten a turn's messages into a labelled transcript for the LLM.

    `system` messages are dropped (they're meta-instructions for the agent,
    not user content). Empty / whitespace-only messages are skipped.
    """
    lines: list[str] = []
    for m in messages:
        if m.role == "system":
            continue
        content = (m.content or "").strip()
        if not content:
            continue
        lines.append(f"{m.role.upper()}: {content}")
    return "\n\n".join(lines)


_VALID_TYPES = frozenset(("fact", "preference", "opinion", "event"))


def _validate_memory(item: Any) -> ExtractedMemory:
    if not isinstance(item, dict):
        raise ExtractionError(f"memory is not a dict: {item!r}")
    missing = {"type", "key", "value", "confidence"} - item.keys()
    if missing:
        raise ExtractionError(f"memory missing fields {sorted(missing)}: {item!r}")
    if item["type"] not in _VALID_TYPES:
        raise ExtractionError(f"invalid type {item['type']!r}: {item!r}")
    if not isinstance(item["key"], str) or not item["key"].strip():
        raise ExtractionError(f"invalid key: {item!r}")
    if not isinstance(item["value"], str) or not item["value"].strip():
        raise ExtractionError(f"invalid value: {item!r}")
    try:
        conf = float(item["confidence"])
    except (TypeError, ValueError) as e:
        raise ExtractionError(f"invalid confidence {item['confidence']!r}: {item!r}") from e
    if not 0.0 <= conf <= 1.0:
        raise ExtractionError(f"confidence out of [0,1]: {conf}")
    return ExtractedMemory(
        type=item["type"],  # type: ignore[typeddict-item]
        key=item["key"].strip(),
        value=item["value"].strip(),
        confidence=conf,
    )


# --- extractor implementations -------------------------------------------


class Extractor(Protocol):
    async def extract(self, messages: list[Message]) -> list[ExtractedMemory]: ...


class NoopExtractor:
    """Used when ANTHROPIC_API_KEY is unset. Always returns []."""

    async def extract(self, messages: list[Message]) -> list[ExtractedMemory]:
        return []


class ClaudeExtractor:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        model: str,
        confidence_floor: float = 0.4,
        max_tokens: int = 1024,
    ) -> None:
        self.client = client
        self.model = model
        self.confidence_floor = confidence_floor
        self.max_tokens = max_tokens

    async def extract(self, messages: list[Message]) -> list[ExtractedMemory]:
        transcript = _render_transcript(messages)
        if not transcript:
            return []

        try:
            resp = await self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                tools=[TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": "record_memories"},
                messages=[{"role": "user", "content": transcript}],
            )
        except anthropic.APIError as e:
            raise ExtractionError(f"Anthropic API error: {e}") from e

        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "record_memories":
                raw = block.input.get("memories") if isinstance(block.input, dict) else None
                if not isinstance(raw, list):
                    raise ExtractionError(f"tool input.memories not a list: {block.input!r}")
                out: list[ExtractedMemory] = []
                for item in raw:
                    try:
                        m = _validate_memory(item)
                    except ExtractionError as e:
                        log.warning("dropping invalid memory: %s", e)
                        continue
                    if m["confidence"] >= self.confidence_floor:
                        out.append(m)
                return out

        raise ExtractionError("no record_memories tool_use block in response")


# --- persistence ---------------------------------------------------------


async def persist_memories(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    source_session: str,
    source_turn: uuid.UUID,
    memories: list[ExtractedMemory],
) -> None:
    """Insert a batch of extracted memories.

    No supersession yet — every memory lands as `active=true`, `supersedes=null`.
    Duplicate (user_id, key) pairs are allowed for now; deduping/contradiction
    handling is a later commit.
    """
    if not memories:
        return
    await conn.executemany(
        """
        INSERT INTO memories
            (user_id, type, key, value, confidence, source_session, source_turn)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        [
            (
                user_id,
                m["type"],
                m["key"],
                m["value"],
                m["confidence"],
                source_session,
                source_turn,
            )
            for m in memories
        ],
    )
