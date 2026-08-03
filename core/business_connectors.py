import json
from pathlib import Path

from database.database import db


class BusinessConnectors:
    """Checks whether external-system connectors (products, accounting,
    orders) are enabled for a company.

    Historically this only read a single static config/business_connectors.json
    shared by every company. Every method now accepts an optional
    company_id and, when given one, looks up that company's rows in the
    `business_connectors` DB table first. A company with no rows
    configured yet (or company_id=None, for callers that can't resolve
    one) falls back to exactly the static file's values -- the original,
    pre-DB behavior -- so nothing regresses for companies that haven't
    been migrated/configured.

    NOTE: `business_connectors.configuration_encrypted` (actual connector
    credentials) is intentionally not decrypted/used here yet -- there is
    no token_crypto module on this branch to decrypt it with. Only the
    row's `status` (enabled/disabled) is read for now. Wiring real
    provider credentials through configuration_encrypted is a follow-up.
    """

    CONFIG_FILE = Path("config") / "business_connectors.json"

    def load_config(self) -> dict:
        if not self.CONFIG_FILE.exists():
            return {}

        with open(self.CONFIG_FILE, "r", encoding="utf-8") as file:
            return json.load(file)

    def _static_default_enabled(self, connector_name: str) -> bool:
        config = self.load_config()
        return bool(config.get(connector_name, {}).get("enabled", False))

    def _db_connectors(self, company_id) -> dict:
        if company_id is None:
            return {}

        try:
            return db.get_business_connectors(company_id)
        except Exception:
            # DB unavailable/not migrated yet -- behave like no rows exist.
            return {}

    def is_enabled(
        self,
        connector_name: str,
        company_id: int | None = None,
    ) -> bool:
        connectors = self._db_connectors(company_id)
        row = connectors.get(connector_name)

        if row is not None:
            return str(row.get("status")) == "active"

        return self._static_default_enabled(connector_name)

    def get_product_info(
        self,
        query: str,
        company_id: int | None = None,
    ) -> dict:
        if not self.is_enabled("products", company_id):
            return {
                "ok": False,
                "connected": False,
                "message_ar": "نقدر نتحقق من المنتج لاحقاً عند ربط المخزون أو الموقع.",
                "message_en": "Product lookup can be enabled later when inventory or website integration is connected."
            }

        return {
            "ok": False,
            "connected": True,
            "message_ar": "لم يتم تنفيذ مزود المنتجات بعد.",
            "message_en": "Products provider is not implemented yet."
        }

    def get_customer_balance(
        self,
        customer_ref: str | None = None,
        company_id: int | None = None,
    ) -> dict:
        if not self.is_enabled("accounting", company_id):
            return {
                "ok": False,
                "connected": False,
                "message_ar": "ميزة الحسابات تحتاج ربط برنامج المحاسبة أولاً.",
                "message_en": "Accounting lookup requires connecting the accounting system first."
            }

        return {
            "ok": False,
            "connected": True,
            "message_ar": "لم يتم تنفيذ مزود المحاسبة بعد.",
            "message_en": "Accounting provider is not implemented yet."
        }

    def get_order_status(
        self,
        order_ref: str | None = None,
        company_id: int | None = None,
    ) -> dict:
        if not self.is_enabled("orders", company_id):
            return {
                "ok": False,
                "connected": False,
                "message_ar": "ميزة تتبع الطلبات تحتاج ربط الموقع أو نظام الطلبات أولاً.",
                "message_en": "Order tracking requires website or orders system integration first."
            }

        return {
            "ok": False,
            "connected": True,
            "message_ar": "لم يتم تنفيذ مزود الطلبات بعد.",
            "message_en": "Orders provider is not implemented yet."
        }


business_connectors = BusinessConnectors()
