"""The sections one company offers, as the engine renders them.

This used to read ``config/business_modules.json`` — a single file on disk
holding one company's departments (Sales, Accounting, Maintenance, IPTV,
Information) — and hand them to every company on the platform. A customer
messaging a clinic's page was shown an IPTV menu, and no company could describe
its own sections without overwriting everybody else's.

The departments now come from the ``business_departments`` table inside the
asking company's own database, so every method needs to be told whose menu to
build. The company is passed explicitly rather than carried in an ambient
context: the engine already has it on the request, and an implicit company is
precisely how the old leak stayed invisible.

A company with no departments defined gets empty results, and the caller omits
the menu. There is no built-in fallback list, because a fallback here would be
somebody else's business.
"""

from __future__ import annotations

from typing import Any

from backend.services.business_department_service import business_department_service


class BusinessModules:
    def load_modules(self, company_id: int | None) -> list[dict[str, Any]]:
        """Every department this company has defined, enabled or not."""
        if not company_id:
            return []

        return [
            self._as_module(row)
            for row in business_department_service.for_assistant(company_id)
        ]

    def enabled_modules(self, company_id: int | None) -> list[dict[str, Any]]:
        return [
            module
            for module in self.load_modules(company_id)
            if module.get("enabled", True)
        ]

    def buttons(self, company_id: int | None, language: str) -> list[str]:
        key = "button_ar" if language == "ar" else "button_en"

        return [
            module[key]
            for module in self.enabled_modules(company_id)
            if module.get(key)
        ]

    def overview_text(self, company_id: int | None, language: str) -> str:
        """The "available sections" sentence, or an empty string.

        Empty rather than a stub sentence: a company that has defined no
        sections has nothing to list, and naming sections it does not offer
        would be inventing its business for it.
        """
        modules = self.enabled_modules(company_id)

        if language == "en":
            names = [m.get("name_en") or m.get("name_ar") for m in modules]
            names = [name for name in names if name]

            if not names:
                return ""

            return "Available sections: " + ", ".join(names) + "."

        names = [m.get("name_ar") or m.get("name_en") for m in modules]
        names = [name for name in names if name]

        if not names:
            return ""

        return "الأقسام المتاحة: " + "، ".join(names) + "."

    def get_module_by_button(
        self,
        company_id: int | None,
        button_text: str,
        language: str,
    ) -> dict[str, Any] | None:
        key = "button_ar" if language == "ar" else "button_en"

        for module in self.enabled_modules(company_id):
            if button_text and button_text == module.get(key):
                return module

        return None

    @staticmethod
    def _as_module(row: dict[str, Any]) -> dict[str, Any]:
        """The shape the engine already works with.

        ``id`` is the department code, not the row id: the engine stores it in
        the session and compares it against the department the model reports,
        both of which are codes. Handing over a row id here would break routing
        in a way that only shows up as a conversation quietly losing its
        department.
        """
        return {
            "id": row.get("code"),
            "code": row.get("code"),
            "enabled": bool(row.get("enabled", True)),
            "name_ar": row.get("name_ar"),
            "name_en": row.get("name_en"),
            "button_ar": row.get("button_ar"),
            "button_en": row.get("button_en"),
        }


business_modules = BusinessModules()
