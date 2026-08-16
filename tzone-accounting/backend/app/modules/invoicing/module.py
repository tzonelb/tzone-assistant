"""Invoicing.

The entire server side of a document type is its declaration. Storage, replication, validation,
legal numbering and the aging report are all provided by `documents`; the posting rules that
turn an invoice into a balanced journal entry live on the client, because they must run offline
(docs/ARCHITECTURE.md).
"""

from __future__ import annotations

from ...core.registry import Registry
from ..documents.types import DocumentType

SALES_INVOICE = DocumentType(
    key="sales_invoice",
    prefix="SI",
    label_en="Sales invoice",
    label_ar="فاتورة مبيعات",
    module="invoicing",
    settles="receivable",
    role="charge",
    sequence=10,
)

PURCHASE_INVOICE = DocumentType(
    key="purchase_invoice",
    prefix="PI",
    label_en="Purchase invoice",
    label_ar="فاتورة مشتريات",
    module="invoicing",
    settles="payable",
    role="charge",
    sequence=20,
)


def setup(registry: Registry) -> None:
    registry.on("document_types", lambda: SALES_INVOICE)
    registry.on("document_types", lambda: PURCHASE_INVOICE)
