"""Pydantic request/response models for the HTTP contract.

Field names, types, and shapes here are the contract. Treat changes as
breaking until the consumers are shown to tolerate them.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# --- shared ---------------------------------------------------------------


class Message(BaseModel):
    """One turn message. `name` is only meaningful for `role="tool"`."""

    model_config = ConfigDict(extra="ignore")

    role: Literal["user", "assistant", "tool", "system"]
    content: str
    name: str | None = None


# --- POST /turns ----------------------------------------------------------


class TurnIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str
    user_id: str | None = None
    messages: list[Message]
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class TurnOut(BaseModel):
    id: str


# --- POST /recall ---------------------------------------------------------


class RecallIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: str
    session_id: str
    user_id: str | None = None
    max_tokens: Annotated[int, Field(gt=0, le=32_000)] = 1024


class Citation(BaseModel):
    turn_id: str
    score: float
    snippet: str


class RecallOut(BaseModel):
    context: str
    citations: list[Citation] = Field(default_factory=list)


# --- POST /search ---------------------------------------------------------


class SearchIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: str
    session_id: str | None = None
    user_id: str | None = None
    limit: Annotated[int, Field(gt=0, le=100)] = 10


class SearchHit(BaseModel):
    content: str
    score: float
    session_id: str
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchOut(BaseModel):
    results: list[SearchHit] = Field(default_factory=list)


# --- GET /users/{user_id}/memories ----------------------------------------

MemoryType = Literal["fact", "preference", "opinion", "event"]


class Memory(BaseModel):
    id: str
    type: MemoryType
    key: str
    value: str
    confidence: float
    source_session: str
    source_turn: str
    created_at: datetime
    updated_at: datetime
    supersedes: str | None = None
    active: bool = True


class MemoriesOut(BaseModel):
    memories: list[Memory] = Field(default_factory=list)


# --- misc -----------------------------------------------------------------


class HealthOut(BaseModel):
    status: Literal["ok"] = "ok"


class ErrorOut(BaseModel):
    error: str
    detail: str | None = None
