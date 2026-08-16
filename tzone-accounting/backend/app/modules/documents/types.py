"""The document-type registry — the seam that makes new paperwork a module, not a patch.

A module contributes a type by returning a `DocumentType` from a `document_types` hook:

    registry.on("document_types", lambda: DocumentType(
        key="credit_note", prefix="CN", label_en="Credit note", label_ar="إشعار دائن",
        module="sales_returns", settles="receivable",
    ))

Storage, replication, legal numbering and the aging feed then work for it with no further
changes here. Note that `documents.doc_type` deliberately has no CHECK constraint: the set of
valid types is whatever is installed, and the database must not need a migration to learn a new
one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class DocumentType:
    key: str
    prefix: str
    label_en: str
    label_ar: str
    module: str
    # 'receivable' / 'payable' put the document into that aging report; a settlement document
    # (a receipt against receivables) reduces the balance of the invoices it allocates to.
    settles: Literal["receivable", "payable"] | None = None
    role: Literal["charge", "settlement"] = "charge"
    # Documents that a tax authority expects to be gapless get a server-assigned number.
    legal_numbering: bool = True
    sequence: int = 100

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "prefix": self.prefix,
            "label_en": self.label_en,
            "label_ar": self.label_ar,
            "module": self.module,
            "settles": self.settles,
            "role": self.role,
            "legal_numbering": self.legal_numbering,
            "sequence": self.sequence,
        }
