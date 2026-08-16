"""Financial reporting endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...core.registry import Registry
from ...db import read_only
from ...security import current_user
from . import calculators

router = APIRouter(prefix="/api/reports", tags=["reports"])


def setup(registry: Registry) -> None:
    registry.add_router(router)


@router.get("/trial-balance")
def trial_balance(
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
    user: dict = Depends(current_user),
) -> dict:
    with read_only() as conn:
        return calculators.trial_balance(conn, date_from, date_to)


@router.get("/profit-and-loss")
def profit_and_loss(
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
    user: dict = Depends(current_user),
) -> dict:
    with read_only() as conn:
        return calculators.profit_and_loss(conn, date_from, date_to)


@router.get("/balance-sheet")
def balance_sheet(as_of: str, user: dict = Depends(current_user)) -> dict:
    with read_only() as conn:
        return calculators.balance_sheet(conn, as_of)


@router.get("/general-ledger")
def general_ledger(
    account_id: str,
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
    user: dict = Depends(current_user),
) -> dict:
    with read_only() as conn:
        return calculators.general_ledger(conn, account_id, date_from, date_to)
