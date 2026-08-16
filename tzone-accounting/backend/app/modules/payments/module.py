"""Receipts and payments.

`role='settlement'` is what makes `payload.allocations` reduce the outstanding balance of the
charge documents they name, in every aging report — without this module and `invoicing` knowing
anything about each other.
"""

from __future__ import annotations

from ...core.registry import Registry
from ..documents.types import DocumentType

RECEIPT = DocumentType(
    key="receipt",
    prefix="RC",
    label_en="Receipt",
    label_ar="سند قبض",
    module="payments",
    settles="receivable",
    role="settlement",
    sequence=30,
)

PAYMENT = DocumentType(
    key="payment",
    prefix="PM",
    label_en="Payment",
    label_ar="سند صرف",
    module="payments",
    settles="payable",
    role="settlement",
    sequence=40,
)


def setup(registry: Registry) -> None:
    registry.on("document_types", lambda: RECEIPT)
    registry.on("document_types", lambda: PAYMENT)
