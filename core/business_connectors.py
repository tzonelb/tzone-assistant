"""Verified facts the assistant is allowed to state.

``core/ai_router.py`` refuses to let the model talk about prices, stock or
balances on its own. Its guardrail looks for a connector result marked
``{"ok": True, ...}``; without one, a price or stock question is answered with
"I cannot confirm that" and escalated to a human.

So everything here answers one question: is this a fact, or is it not? A
connector that cannot prove something returns ``ok: False`` and the guardrail
does its job. The only connector that can currently prove anything is the
product catalogue, which is first-party data living in the company's own
database rather than an integration that has to be configured.

Multi-tenancy note: ``get_product_info`` answers from exactly one company's
catalogue and needs that company's id to do it. Called without one it returns no
facts at all, because the alternative — guessing a company — is how one
company's customer gets quoted another company's prices.

``config/business_connectors.json`` gates the *external* providers (a website,
an inventory system, an accounting package). It does not gate the catalogue: a
company that typed its products into this platform has already connected them.
"""

import json
import logging
from pathlib import Path

from backend.services.catalogue_service import catalogue_service


logger = logging.getLogger(__name__)


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

    def get_product_info(
        self,
        query: str,
        company_id: int | None = None,
    ) -> dict:
        """Look a customer's product question up in that company's catalogue.

        Returns ``ok: True`` only when this company sells something the message
        actually names, and — for a price question — only when the matched rows
        carry a confirmed price. A row with no price is not a price fact, and
        marking it as one would switch the price guardrail off while leaving the
        model with nothing real to quote.
        """
        if not company_id:
            logger.warning(
                "Product lookup ran with no company; the assistant gets no "
                "product facts rather than another company's catalogue."
            )

            return self.catalogue_unavailable()

        try:
            products = catalogue_service.search_for_assistant(
                company_id=int(company_id),
                query=query,
            )
        except Exception:
            logger.exception(
                "Product lookup failed for company %s", company_id
            )

            return self.catalogue_unavailable()

        if not products:
            return {
                "ok": False,
                "connected": True,
                "connector": "products",
                "source": "catalogue",
                "company_id": int(company_id),
                "products": [],
                "message_ar": (
                    "ما لقيت هالمنتج بكتالوج الشركة، فما فيني أكد سعره أو توفره."
                ),
                "message_en": (
                    "No product in this company's catalogue matches that "
                    "request, so its price and availability are not confirmed."
                ),
            }

        if catalogue_service.message_asks_price(query) and not any(
            product["price_confirmed"] for product in products
        ):
            return {
                "ok": False,
                "connected": True,
                "connector": "products",
                "source": "catalogue",
                "company_id": int(company_id),
                "products": products,
                "message_ar": (
                    "المنتج موجود بالكتالوج بس سعره مش مسجّل، فما فيني أكد السعر."
                ),
                "message_en": (
                    "The product is in the catalogue but has no published "
                    "price, so the price is not confirmed."
                ),
            }

        in_stock = sum(1 for product in products if product["in_stock"])

        return {
            "ok": True,
            "connected": True,
            "connector": "products",
            "source": "catalogue",
            "company_id": int(company_id),
            "products": products,
            "message_ar": (
                f"{len(products)} منتج من كتالوج الشركة، "
                f"{in_stock} منها متوفر. الأسعار والتوفر مأكدة من قاعدة البيانات."
            ),
            "message_en": (
                f"{len(products)} matching product(s) from this company's own "
                f"catalogue, {in_stock} in stock. Prices, currency and stock "
                "below are confirmed records — state them exactly as given and "
                "do not round, convert or estimate them."
            ),
        }

    def catalogue_unavailable(self) -> dict:
        """No facts, phrased for whichever external provider might exist."""
        if not self.is_enabled("products"):
            return {
                "ok": False,
                "connected": False,
                "connector": "products",
                "products": [],
                "message_ar": "نقدر نتحقق من المنتج لاحقاً عند ربط المخزون أو الموقع.",
                "message_en": "Product lookup can be enabled later when inventory or website integration is connected."
            }

        return {
            "ok": False,
            "connected": True,
            "connector": "products",
            "products": [],
            "message_ar": "لم يتم تنفيذ مزود المنتجات الخارجي بعد.",
            "message_en": "The external products provider is not implemented yet."
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
