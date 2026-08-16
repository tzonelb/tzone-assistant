"""Login, device registration and the current user."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ...db import read_only, transaction, utcnow
from ...security import create_token, current_user, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no look-alike characters


class LoginRequest(BaseModel):
    username: str
    password: str
    device_id: str | None = None
    device_label: str = ""


class LoginResponse(BaseModel):
    token: str
    user: dict
    device: dict | None = None


def _mint_device_code(conn) -> str:
    """Two characters cover 1024 terminals; beyond that it widens rather than collide."""
    used = {row["device_code"] for row in conn.execute("SELECT device_code FROM devices")}
    for first in _ALPHABET:
        for second in _ALPHABET:
            candidate = f"{first}{second}"
            if candidate not in used:
                return candidate
    return uuid.uuid4().hex[:3].upper()


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    with transaction() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1", (payload.username,)
        ).fetchone()
        if row is None or not verify_password(payload.password, row["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )

        device = None
        if payload.device_id:
            existing = conn.execute(
                "SELECT * FROM devices WHERE id = ?", (payload.device_id,)
            ).fetchone()
            if existing is None:
                code = _mint_device_code(conn)
                conn.execute(
                    "INSERT INTO devices (id, user_id, label, device_code, created_at,"
                    " last_seen) VALUES (?,?,?,?,?,?)",
                    (payload.device_id, row["id"], payload.device_label, code, utcnow(), utcnow()),
                )
                device = {"id": payload.device_id, "device_code": code}
            else:
                conn.execute(
                    "UPDATE devices SET last_seen = ?,"
                    " label = COALESCE(NULLIF(?,''), label) WHERE id = ?",
                    (utcnow(), payload.device_label, payload.device_id),
                )
                device = {"id": existing["id"], "device_code": existing["device_code"]}

        token = create_token(row["id"], row["username"], row["role"], payload.device_id)

    return LoginResponse(
        token=token,
        user={
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
        },
        device=device,
    )


@router.get("/me")
def me(user: dict = Depends(current_user)) -> dict:
    with read_only() as conn:
        row = conn.execute(
            "SELECT id, username, display_name, role FROM users WHERE id = ?", (user["sub"],)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")
    return dict(row)
