"""Unit tests for the recall renderer.

No DB, no HTTP — exercises ``render_context`` directly with synthetic
``MemoryRow`` lists. Covers section ordering, citation alignment,
truncation under tight budgets, and the bullet-formatting heuristics.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from memory_service.recall import MemoryRow, render_context


def _row(
    type_: str,
    key: str,
    value: str,
    confidence: float = 0.9,
    updated_at: datetime | None = None,
) -> MemoryRow:
    return MemoryRow(
        id=uuid.uuid4(),
        type=type_,  # type: ignore[arg-type]
        key=key,
        value=value,
        confidence=confidence,
        source_turn=uuid.uuid4(),
        updated_at=updated_at or datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
    )


def test_empty_memories_returns_empty_context() -> None:
    ctx, cites = render_context([], max_tokens=512)
    assert ctx == ""
    assert cites == []


def test_zero_or_negative_budget_returns_empty() -> None:
    rows = [_row("fact", "employer", "Notion")]
    assert render_context(rows, max_tokens=0) == ("", [])
    assert render_context(rows, max_tokens=-1) == ("", [])


def test_all_four_sections_render_in_priority_order() -> None:
    rows = [
        _row("fact", "employer", "Notion"),
        _row("preference", "ide", "Cursor"),
        _row("opinion", "ts_view", "TS overkill for scripts"),
        _row("event", "career_change", "Joined Notion"),
    ]
    ctx, cites = render_context(rows, max_tokens=512)
    assert "## Known facts about this user" in ctx
    assert "## Preferences" in ctx
    assert "## Opinions" in ctx
    assert "## Significant events" in ctx
    # Order: facts before preferences before opinions before events.
    assert (
        ctx.index("## Known facts about this user")
        < ctx.index("## Preferences")
        < ctx.index("## Opinions")
        < ctx.index("## Significant events")
    )
    assert len(cites) == 4


def test_short_value_gets_key_parenthetical() -> None:
    ctx, _ = render_context([_row("fact", "city", "Berlin")], max_tokens=200)
    assert "- Berlin (city)" in ctx


def test_long_sentence_value_skips_key_parenthetical() -> None:
    rows = [_row("event", "career_change", "Left Stripe and started at Notion as PM")]
    ctx, _ = render_context(rows, max_tokens=200)
    assert "(career_change)" not in ctx
    assert "Left Stripe and started at Notion as PM" in ctx


def test_event_bullet_has_date_prefix() -> None:
    rows = [
        _row(
            "event",
            "moved",
            "Moved",
            updated_at=datetime(2026, 3, 15, tzinfo=UTC),
        )
    ]
    ctx, _ = render_context(rows, max_tokens=200)
    assert "- [2026-03-15]" in ctx


def test_citations_match_rendered_bullets_in_order() -> None:
    rows = [
        _row("fact", "employer", "Notion"),
        _row("preference", "ide", "Cursor"),
        _row("event", "career_change", "Joined Notion"),
    ]
    ctx, cites = render_context(rows, max_tokens=512)
    assert [c.snippet for c in cites] == ["Notion", "Cursor", "Joined Notion"]
    assert all(c.score > 0 for c in cites)
    bullets = [line for line in ctx.splitlines() if line.startswith("- ")]
    assert len(bullets) == len(cites)


def test_tight_budget_drops_lowest_priority_first() -> None:
    rows = [
        _row("fact", "employer", "Notion"),
        _row("fact", "city", "Berlin"),
        _row("preference", "ide", "Cursor"),
        _row("event", "career_change", "Joined Notion"),
    ]
    # Budget large enough for the two facts header + 2 bullets, not more.
    # ~ "## Known facts about this user" header + two short bullets.
    ctx, cites = render_context(rows, max_tokens=20)
    # Facts come first, so they should win the tight budget.
    snippets = [c.snippet for c in cites]
    # Whatever made it in must be facts (highest priority).
    assert all(s in {"Notion", "Berlin"} for s in snippets)
    # Shouldn't have leaked into preference/event sections.
    assert "## Preferences" not in ctx
    assert "## Significant events" not in ctx


def test_budget_too_small_for_any_bullet_returns_empty() -> None:
    rows = [_row("fact", "employer", "A very long employer name indeed")]
    ctx, cites = render_context(rows, max_tokens=1)
    assert ctx == ""
    assert cites == []


def test_only_sections_with_content_appear() -> None:
    rows = [_row("preference", "lang", "Python")]
    ctx, _ = render_context(rows, max_tokens=200)
    assert "## Preferences" in ctx
    assert "## Known facts about this user" not in ctx
    assert "## Opinions" not in ctx
    assert "## Significant events" not in ctx
