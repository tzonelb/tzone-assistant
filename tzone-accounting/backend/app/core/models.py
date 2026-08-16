"""Kernel wire models. Entity payloads stay untyped `dict` on purpose — the kernel replicates
whatever a module declares, and pinning a union of entity shapes here would mean editing the
kernel every time a module is added."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SyncOp(BaseModel):
    seq: int
    entity: str
    id: str
    op: Literal["upsert", "delete"] = "upsert"
    record: dict[str, Any] = Field(default_factory=dict)


class SyncPushRequest(BaseModel):
    device_id: str
    ops: list[SyncOp]


class SyncRejection(BaseModel):
    seq: int
    id: str
    entity: str
    reason: str


class SyncPushResponse(BaseModel):
    accepted: list[int]
    rejected: list[SyncRejection]
    cursor: int
    # record id -> field overrides the server assigned (e.g. a gapless legal number)
    assigned: dict[str, dict[str, Any]] = Field(default_factory=dict)


class SyncChange(BaseModel):
    entity: str
    change_seq: int
    record: dict[str, Any]


class SyncPullResponse(BaseModel):
    changes: list[SyncChange]
    cursor: int
    has_more: bool
