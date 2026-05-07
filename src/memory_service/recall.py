"""Render a user's active memories into a recall-context block.

Pure logic, no DB or HTTP — handed a list of `MemoryRow` and a token
budget, returns ``(context_text, citations)``. Kept separate from the
FastAPI handler so it can be unit-tested without a live stack.

Token counting is intentionally rough (chars/4). Anthropic counts BPE
tokens differently; this is good enough for a soft budget. The spec
says "don't blow past it by 2×" — that bar this design clears comfortably.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from memory_service.schemas import Citation

MemoryType = Literal["fact", "preference", "opinion", "event"]


@dataclass(frozen=True)
class MemoryRow:
    """Subset of the `memories` row needed for rendering. Mirrors the SQL
    projection in api/recall.py — kept as a dataclass (not the Pydantic
    `Memory` model) so the renderer stays decoupled from HTTP schemas."""

    id: uuid.UUID
    type: MemoryType
    key: str
    value: str
    confidence: float
    source_turn: uuid.UUID
    updated_at: datetime


# Section header per type. Order in this dict is the priority order.
_SECTION_HEADERS: dict[MemoryType, str] = {
    "fact": "## Known facts about this user",
    "preference": "## Preferences",
    "opinion": "## Opinions",
    "event": "## Significant events",
}


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars/token. Floors at 1 for non-empty."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _bullet(memory: MemoryRow) -> str:
    """Render a single memory as a markdown bullet line.

    The trailing `(key)` parenthetical disambiguates short values like
    "Notion" → "- Notion (employer)". Sentence-shaped values
    (>4 whitespace-separated tokens) skip it. Events get a date prefix.
    """
    value = memory.value.strip()
    short_value = len(value.split()) <= 4
    line = value
    if short_value:
        line = f"{value} ({memory.key})"
    if memory.type == "event":
        line = f"[{memory.updated_at.date().isoformat()}] {line}"
    return f"- {line}"


def render_context(
    memories: list[MemoryRow],
    max_tokens: int,
    *,
    score_lookup: dict[uuid.UUID, float] | None = None,
) -> tuple[str, list[Citation]]:
    """Render memories into a markdown context block and matching citations.

    Memories are expected pre-sorted by the caller (M10's type-priority
    ordering, or M14b's RRF ordering). The renderer groups them by type,
    preserving incoming order within each section. Truncates by dropping
    bullets in the tail of each section once the token budget is hit;
    citations only cover what's actually in the rendered text.

    If ``score_lookup`` is provided, each citation's ``score`` is taken
    from that map (keyed by memory id) — used by M14b to surface RRF
    scores. Without it, citations fall back to the memory's confidence.
    """
    if not memories or max_tokens <= 0:
        return "", []

    # Group preserving incoming order.
    by_type: dict[MemoryType, list[MemoryRow]] = {t: [] for t in _SECTION_HEADERS}
    for m in memories:
        by_type.setdefault(m.type, []).append(m)

    chunks: list[str] = []
    citations: list[Citation] = []
    used = 0

    for mtype in _SECTION_HEADERS:  # iteration order = priority
        rows = by_type.get(mtype) or []
        if not rows:
            continue

        header = _SECTION_HEADERS[mtype]
        header_cost = _estimate_tokens(header) + 1  # +1 for the newline
        section_started = False

        for m in rows:
            line = _bullet(m)
            line_cost = _estimate_tokens(line) + 1
            extra = (header_cost if not section_started else 0) + line_cost
            if used + extra > max_tokens:
                break

            if not section_started:
                if chunks:  # blank line between sections
                    chunks.append("")
                    used += 1
                chunks.append(header)
                used += header_cost
                section_started = True

            chunks.append(line)
            used += line_cost
            score = (
                score_lookup[m.id]
                if score_lookup is not None and m.id in score_lookup
                else float(m.confidence)
            )
            citations.append(
                Citation(
                    turn_id=str(m.source_turn),
                    score=score,
                    snippet=m.value,
                )
            )

        # If budget already exhausted, no point checking later sections.
        if used >= max_tokens:
            break

    if not citations:
        return "", []
    return "\n".join(chunks), citations
