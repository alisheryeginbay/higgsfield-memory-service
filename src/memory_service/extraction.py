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
You derive durable atomic memories about the USER (never the assistant) from
one conversation turn. The output feeds an agent's long-term memory store,
so prefer high-signal, agent-relevant assertions. Routine chitchat, day-to-day
activity, and speculation become noise that hurts the agent later — when in
doubt, omit.

<categories>
- fact: stable, verifiable user info an agent might reuse across sessions —
  employer, role, city, country, family, pets, demographics, languages,
  allergies, schedule constraints.
- preference: lasting like / dislike / stated tendency that shapes how the
  agent should respond — "I prefer concise answers", "I don't eat meat",
  "I always use TypeScript in production".
- opinion: subjective view on a topic that may evolve — "I think Cursor is
  overrated", "Rust generics are confusing".
- event: a SIGNIFICANT life change or committed plan that future sessions
  should know — career move, relocation, marriage, scheduled deadline,
  diagnosis. NOT day-to-day activities (cooking attempts, walks, weekend
  plans without specifics, current mood, work-in-progress hobbies).
</categories>

<rules>
- Memories describe the user. Skip the assistant's words.
- Skip generic chitchat (greetings, pleasantries, "thanks", "ok").
- Use canonical snake_case keys when obvious: `employer`, `role`, `city`,
  `country`, `language_preference`, `pet:dog:name`, `pet:dog:breed`,
  `dietary_restriction`, `ide_preference`. Otherwise pick a short stable
  snake_case key.
- `value` is a concise assertion ("Notion", "Berlin", "Python for scripts"),
  not a sentence.
- Confidence anchors:
    * 0.90-1.00 — explicit, unambiguous user statement.
    * 0.70-0.89 — clear implication from context (walking Biscuit → has dog).
    * 0.40-0.69 — soft inference from indirect cues.
    * below 0.40 — guess; OMIT.
- Self-correction in the same turn ("I love TS — actually I prefer Python
  for scripts") produces ONE memory reflecting the corrected (final)
  preference. Do not store the rejected version.
- A single mention of an activity is not a lasting preference. "I'm trying
  sourdough" is not "user has baking interest".
- Never speculate about traits, demographics, or relationships not stated by
  the user.
- If nothing in the turn meets these bars, call record_memories with [].
</rules>

<examples>
<example>
<turn>
USER: Quick context — I'm a senior PM at Notion, based in Berlin. We have a corgi mix named Biscuit.
ASSISTANT: Got it.
</turn>
<extracted>
[
  {"type":"fact","key":"employer","value":"Notion","confidence":0.95},
  {"type":"fact","key":"role","value":"Senior PM","confidence":0.95},
  {"type":"fact","key":"city","value":"Berlin","confidence":0.95},
  {"type":"fact","key":"pet:dog:name","value":"Biscuit","confidence":0.95},
  {"type":"fact","key":"pet:dog:breed","value":"corgi mix","confidence":0.9}
]
</extracted>
</example>

<example>
<turn>
USER: I'm trying to nail a sourdough starter. Day 4, smells fine but no rise yet. Also thinking of a long hike Saturday if the weather holds.
ASSISTANT: Day 4 is normal.
</turn>
<extracted>
[]
</extracted>
<reasoning>The starter and the hike are routine activity / weekend plans without specifics. Neither is a durable user trait, preference, or significant life event. A future agent does not benefit from "user was on day 4 of a sourdough starter on this date". Omit.</reasoning>
</example>

<example>
<turn>
USER: I love TypeScript — actually scratch that. For quick scripts I always reach for Python. TS is overkill there.
ASSISTANT: Makes sense.
</turn>
<extracted>
[
  {"type":"preference","key":"script_language_preference","value":"Python for quick scripts","confidence":0.95}
]
</extracted>
<reasoning>The "love TypeScript" claim is rejected by the user mid-sentence — drop it. Only the corrected preference is stored. The user's note that they keep TS for non-script work is a single mention, not enough to promote to its own preference memory.</reasoning>
</example>

<example>
<turn>
USER: Big news — I just left Stripe and started at Notion last Monday, switching to a PM role.
ASSISTANT: Congrats!
</turn>
<extracted>
[
  {"type":"fact","key":"employer","value":"Notion","confidence":0.95},
  {"type":"fact","key":"role","value":"PM","confidence":0.95},
  {"type":"event","key":"career_change","value":"Left Stripe, started at Notion as PM","confidence":0.95}
]
</extracted>
<reasoning>Current employer / role are durable facts. The career change is a SIGNIFICANT life event the agent should remember (lets it answer "what was your previous role?"). The departure detail is folded into the event rather than duplicated as a separate memory.</reasoning>
</example>
</examples>

Always return through the record_memories tool. Never produce free-form text.
"""

TOOL_SCHEMA: dict[str, Any] = {
    "name": "record_memories",
    "description": (
        "Record durable atomic memories about the USER from one conversation turn. "
        "Use this whenever the turn contains durable facts, lasting preferences, "
        "subjective opinions, or significant life events worth remembering across "
        "sessions. Call with an empty list if the turn is just greetings, "
        "day-to-day activity, transient mood, or otherwise has nothing the agent "
        "should retain — false positives hurt downstream recall more than misses. "
        "Use canonical snake_case keys (employer, city, language_preference, "
        "pet:dog:name, ...) and the confidence anchors documented in the system "
        "prompt; never invent traits the user did not state."
    ),
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


# Types that supersede the existing active memory with the same key.
# Events are inherently temporal — multiple "career_change" rows over time
# are valid, so we never supersede them.
_SUPERSEDED_TYPES: frozenset[str] = frozenset({"fact", "preference", "opinion"})


async def persist_memories(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    source_session: str,
    source_turn: uuid.UUID,
    memories: list[ExtractedMemory],
) -> None:
    """Insert extracted memories, applying per-key supersession.

    For `fact` / `preference` / `opinion`: if an active memory with the same
    `(user_id, key)` exists, mark all such rows inactive and insert the new
    one with `supersedes` pointing at the most recent old row. If the old
    row has the same `value` as the new one, skip the insert entirely
    (idempotent re-statement).

    For `event`: always insert. Events accumulate; we may prune them later.

    Caller is responsible for wrapping this in a transaction.
    """
    if not memories:
        return

    for m in memories:
        if m["type"] in _SUPERSEDED_TYPES:
            existing = await conn.fetchrow(
                """
                SELECT id, value FROM memories
                WHERE user_id = $1 AND key = $2 AND active = TRUE
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                user_id,
                m["key"],
            )

            if existing is not None and existing["value"] == m["value"]:
                # Idempotent re-statement — current state already matches.
                continue

            supersedes_id: uuid.UUID | None = None
            if existing is not None:
                # Close every active row for (user_id, key); defensive against
                # any pre-supersession data that managed to leave duplicates.
                await conn.execute(
                    """
                    UPDATE memories SET active = FALSE, updated_at = now()
                    WHERE user_id = $1 AND key = $2 AND active = TRUE
                    """,
                    user_id,
                    m["key"],
                )
                supersedes_id = existing["id"]

            await conn.execute(
                """
                INSERT INTO memories
                    (user_id, type, key, value, confidence,
                     source_session, source_turn, supersedes)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                user_id,
                m["type"],
                m["key"],
                m["value"],
                m["confidence"],
                source_session,
                source_turn,
                supersedes_id,
            )
        else:
            # Event: bulk semantic. No supersession.
            await conn.execute(
                """
                INSERT INTO memories
                    (user_id, type, key, value, confidence,
                     source_session, source_turn)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                user_id,
                m["type"],
                m["key"],
                m["value"],
                m["confidence"],
                source_session,
                source_turn,
            )
