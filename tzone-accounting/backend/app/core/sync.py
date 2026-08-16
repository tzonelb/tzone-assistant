"""The replication endpoints. Entity-agnostic: every table any module declares syncs here.

Protocol and conflict policy: docs/OFFLINE_SYNC.md.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..config import get_settings
from ..db import current_counter, read_only, transaction, utcnow
from ..security import current_user
from . import entities as ent
from .errors import ValidationError
from .models import (
    SyncChange,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
    SyncRejection,
)
from .registry import get_registry

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/push", response_model=SyncPushResponse)
def push(payload: SyncPushRequest, user: dict = Depends(current_user)) -> SyncPushResponse:
    """Apply a batch of client operations.

    Ops are applied and reported independently: one invalid record must not stop the rest of a
    shop's day from replicating. Rejections come back with a reason so the client can surface
    them — an op is never silently dropped, because that would mean the two ledgers disagree
    without anyone knowing.
    """
    registry = get_registry()
    settings = get_settings()
    if len(payload.ops) > settings.max_batch:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"batch too large: {len(payload.ops)} > {settings.max_batch}",
        )

    accepted: list[int] = []
    rejected: list[SyncRejection] = []
    assigned: dict[str, dict] = {}

    with transaction() as conn:
        for op in sorted(payload.ops, key=lambda o: o.seq):
            descriptor = registry.entities.get(op.entity)
            if descriptor is None:
                rejected.append(
                    SyncRejection(
                        seq=op.seq,
                        id=op.id,
                        entity=op.entity,
                        reason=f"no installed module owns entity {op.entity!r}",
                    )
                )
                continue

            record = {**op.record, "origin": op.record.get("origin") or payload.device_id}
            try:
                # A savepoint keeps one bad op from rolling back the whole batch.
                conn.execute("SAVEPOINT op")
                overrides = ent.apply_op(conn, descriptor, op.id, op.op, record)
                conn.execute("RELEASE SAVEPOINT op")
            except ValidationError as exc:
                conn.execute("ROLLBACK TO SAVEPOINT op")
                conn.execute("RELEASE SAVEPOINT op")
                rejected.append(
                    SyncRejection(seq=op.seq, id=op.id, entity=op.entity, reason=str(exc))
                )
                continue

            accepted.append(op.seq)
            if overrides:
                assigned[op.id] = overrides
            registry.hooks.emit(
                "record_stored",
                conn=conn,
                entity=op.entity,
                record_id=op.id,
                record=record,
                user=user,
            )

        conn.execute(
            "UPDATE devices SET last_seen = ? WHERE id = ?", (utcnow(), payload.device_id)
        )
        registry.hooks.emit(
            "sync_pushed",
            conn=conn,
            device_id=payload.device_id,
            accepted=accepted,
            rejected=[r.model_dump() for r in rejected],
            user=user,
        )
        cursor = current_counter(conn, ent.CHANGE_SEQ)

    return SyncPushResponse(
        accepted=accepted, rejected=rejected, cursor=cursor, assigned=assigned
    )


@router.get("/pull", response_model=SyncPullResponse)
def pull(
    since: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    user: dict = Depends(current_user),
) -> SyncPullResponse:
    """Records whose `change_seq` is greater than `since`, in sequence order.

    The cursor is the server's monotonic counter, not a timestamp: clock skew between
    terminals must never be able to skip a record.
    """
    registry = get_registry()
    candidates: list[tuple[int, str, dict]] = []

    with read_only() as conn:
        for name, descriptor in registry.entities.items():
            rows = conn.execute(
                f"SELECT * FROM {descriptor.table} WHERE change_seq > ?"
                " ORDER BY change_seq LIMIT ?",
                (since, limit),
            ).fetchall()
            for row in rows:
                candidates.append((row["change_seq"], name, ent.row_to_record(descriptor, row)))

        candidates.sort(key=lambda item: item[0])
        window = candidates[:limit]
        has_more = len(candidates) > limit

        for name, descriptor in registry.entities.items():
            if descriptor.child is None:
                continue
            ids = [record["id"] for _, entity, record in window if entity == name]
            children = ent.read_children(conn, descriptor.child, ids)
            for _, entity, record in window:
                if entity == name:
                    record[descriptor.child.payload_key] = children.get(record["id"], [])

    changes = [
        SyncChange(entity=entity, change_seq=seq, record=record)
        for seq, entity, record in window
    ]
    return SyncPullResponse(
        changes=changes,
        cursor=changes[-1].change_seq if changes else since,
        has_more=has_more,
    )


@router.get("/status")
def sync_status(user: dict = Depends(current_user)) -> dict:
    registry = get_registry()
    with read_only() as conn:
        cursor = current_counter(conn, ent.CHANGE_SEQ)
        counts = {
            name: conn.execute(
                f"SELECT COUNT(*) AS n FROM {descriptor.table} WHERE deleted = 0"
            ).fetchone()["n"]
            for name, descriptor in registry.entities.items()
        }
    return {"cursor": cursor, "counts": counts, "server_time": utcnow()}
