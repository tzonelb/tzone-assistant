"""Deciding that a message has moved to a different section of the business.

This module used to carry ``DEPARTMENT_KEYWORDS``: a hardcoded Arabic/English
table naming one company's departments — sales, iptv, maintenance, telecom,
accounting, information — and listing that company's products and vocabulary
under each. It was applied to every company on the platform. A clinic's patient
writing "شاشة" was routed to a maintenance department the clinic does not have,
a company that named its section ``bookings`` could never be detected at all,
and no business could change any of it, because the words were in this file
rather than in that business's own data.

The table is gone. Detection now reads the asking company's own
``business_departments`` rows — the same codes, names and button labels the
customer is actually shown — so a section can only be detected by a company
that defined it, and detecting it means what that company says it means.

The alternative considered was deleting the mechanism outright. It is kept
because it does something the button match cannot: a customer who types the
name of a section instead of pressing its button still lands in it. What it may
never do again is apply one business's vocabulary to another's customers, which
is why every entry point takes a company and returns nothing without one.
"""

from __future__ import annotations

from backend.services.business_department_service import business_department_service


# Below this, a term is too short to be evidence. Matching a two-letter code
# inside an unrelated word would route a conversation on a coincidence.
MIN_TERM_LENGTH = 3


class IntentTransitionManager:
    GENERAL_CHANNELS = [
        "messenger",
        "whatsapp",
        "instagram",
        "website_chat",
    ]

    @staticmethod
    def _terms(department: dict) -> list[str]:
        """Every way this company refers to one of its own sections.

        The code, both names and both button labels — whatever the company
        filled in. A customer types what they were shown, and what they were
        shown is exactly these strings.
        """
        candidates = (
            department.get("code"),
            department.get("name_ar"),
            department.get("name_en"),
            department.get("button_ar"),
            department.get("button_en"),
        )

        terms = []

        for candidate in candidates:
            text = str(candidate or "").strip().lower()

            if len(text) >= MIN_TERM_LENGTH and text not in terms:
                terms.append(text)

        return terms

    def detect_department(
        self,
        message: str,
        company_id: int | None = None,
    ) -> str | None:
        """The code of the section this message names, or ``None``.

        Without a company there is nothing to match against, and that is the
        answer — not a fallback list. The longest match wins so that a company
        with both "sales" and "sales returns" gets the more specific one rather
        than whichever happens to be first in its menu.
        """
        if not message or not company_id:
            return None

        normalized = message.lower().strip()
        best_code = None
        best_length = 0

        for department in business_department_service.for_assistant(company_id):
            code = department.get("code")

            if not code:
                continue

            for term in self._terms(department):
                if term in normalized and len(term) > best_length:
                    best_code = str(code)
                    best_length = len(term)

        return best_code

    def should_switch_to_ai(
        self,
        channel: str,
        message: str,
        current_department: str | None = None,
        company_id: int | None = None,
    ) -> bool:
        if channel not in self.GENERAL_CHANNELS:
            return False

        detected_department = self.detect_department(
            message,
            company_id=company_id,
        )

        if not detected_department:
            return False

        if not current_department:
            return True

        return detected_department != current_department


intent_transition_manager = IntentTransitionManager()
