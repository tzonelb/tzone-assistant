import json
from pathlib import Path


class BusinessConnectors:
    CONFIG_FILE = Path("config") / "business_connectors.json"

    def load_config(self) -> dict:
        if not self.CONFIG_FILE.exists():
            return {}

        with open(self.CONFIG_FILE, "r", encoding="utf-8") as file:
            return json.load(file)

    def is_enabled(self, connector_name: str) -> bool:
        config = self.load_config()
        return bool(config.get(connector_name, {}).get("enabled", False))

    def get_product_info(self, query: str) -> dict:
        if not self.is_enabled("products"):
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

    def get_customer_balance(self, customer_ref: str | None = None) -> dict:
        if not self.is_enabled("accounting"):
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

    def get_order_status(self, order_ref: str | None = None) -> dict:
        if not self.is_enabled("orders"):
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