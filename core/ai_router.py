import json
import logging
import re
from typing import Any

import httpx

from backend.services.business_department_service import business_department_service
from config.settings import config
from core.prompt_builder import prompt_builder


logger = logging.getLogger(__name__)


class AIRouter:
    # The only two department values that do not belong to a company.
    #
    # ``unknown`` is the sentinel for "the model did not decide"; ``human_support``
    # is the escape hatch the guardrails below and the engine's safe fallback
    # both produce. Neither is a section a business defines, so neither is
    # looked for in the company's own list.
    #
    # Everything else comes from ``business_departments`` in the asking
    # company's database. This used to be a hardcoded list of nine — sales,
    # iptv, maintenance, accounting, telecom, orders, information — one
    # company's sections applied to every company on the platform. A business
    # that defined "bookings" had the model correctly answer ``bookings`` and
    # then had that answer thrown away as invalid and rewritten to ``unknown``,
    # so its own vocabulary could never survive the round trip.
    RESERVED_DEPARTMENTS = ("human_support", "unknown")

    def allowed_departments(self, company_id: int | None) -> list[str]:
        """The department codes a reply for this company may carry.

        With no company there is nothing but the two reserved values — guessing
        a company's sections here is the leak this replaces, and answering with
        another company's vocabulary is worse than answering with none.
        """
        return [
            *business_department_service.codes(company_id),
            *self.RESERVED_DEPARTMENTS,
        ]

    def route(
        self,
        message: str,
        channel: str,
        user_id: str,
        language: str | None = None,
        current_state: str | None = None,
        context: dict[str, Any] | None = None,
        knowledge: list[dict] | None = None,
        connector_results: list[dict] | None = None,
        response_policy: dict | None = None,
        match_result: dict[str, Any] | None = None,
        company_id: int | None = None,
        channel_account_id: int | None = None,
    ) -> dict[str, Any] | None:
        if not config.AI_ENABLED:
            return None

        if not config.OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY is missing")
            return None

        if not message:
            return None

        knowledge = knowledge or []
        connector_results = connector_results or []
        context = context or {}
        response_policy = response_policy or {}
        match_result = match_result or {}

        try:
            raw_result = self.call_openai(
                message=message,
                channel=channel,
                user_id=user_id,
                language=language,
                current_state=current_state,
                context=context,
                knowledge=knowledge,
                connector_results=connector_results,
                response_policy=response_policy,
                match_result=match_result,
                company_id=company_id,
                channel_account_id=channel_account_id,
            )

            result = self.normalize_result(raw_result, company_id=company_id)

            return self.apply_guardrails(
                result=result,
                message=message,
                knowledge=knowledge,
                connector_results=connector_results,
            )

        except Exception:
            logger.exception("AI Router failed")
            return None

    def call_openai(
        self,
        message: str,
        channel: str,
        user_id: str,
        language: str | None,
        current_state: str | None,
        context: dict[str, Any],
        knowledge: list[dict],
        connector_results: list[dict],
        response_policy: dict,
        match_result: dict[str, Any],
        company_id: int | None = None,
        channel_account_id: int | None = None,
    ) -> dict[str, Any]:
        # The company's own trained profile — tone, instructions, examples.
        # Without the company this returns a neutral prompt carrying no
        # identity, never another company's.
        company_prompt = prompt_builder.build_system_prompt(
            channel,
            company_id=company_id,
            channel_account_id=channel_account_id,
        )

        grounded_prompt = """
You are the customer-facing AI assistant for a business platform.

Your response must be grounded only in:
1. SELECTED_KNOWLEDGE
2. CONNECTOR_RESULTS
3. Safe conversation context

The current customer message has priority over old context.

IMPORTANT RULES:
- Reply naturally; do not copy a stored FAQ answer mechanically.
- Answer the exact question being asked.
- Use previous conversation only when the message is clearly a follow-up.
- If the user changes topic, immediately follow the new topic.
- Reply in the language of the current customer message.
- You may change language during the same conversation without restarting it.
- Do not invent products, brands, prices, stock, offers, warranties, addresses,
  balances, invoices, order statuses, or repair costs.
- A general knowledge item saying that phones are available does not prove
  which brands are available.
- If the requested fact is missing, clearly say it is not confirmed yet.
- When information is missing, ask one useful and specific follow-up question.
- Do not repeatedly send the same generic answer.
- Do not claim that a human checked something unless connector results confirm it.
- Financial data requires identity verification.
- Keep the answer short and helpful.
- Return JSON only.

The JSON must contain:
{
  "department": "...",
  "intent": "...",
  "topic": "...",
  "language": "ar or en",
  "confidence": 0.0,
  "reply": "...",
  "buttons": [],
  "needs_human": false,
  "missing_information": [],
  "used_knowledge_ids": [],
  "notes": "..."
}
""".strip()

        user_payload = {
            "customer_message": message,
            "channel": channel,
            "user_id": str(user_id),
            "language_hint": language,
            "current_state": current_state,
            "conversation_context": context,
            "knowledge_match": match_result,
            "selected_knowledge": knowledge,
            "connector_results": connector_results,
            "response_policy": response_policy,
        }

        payload = {
            "model": config.OPENAI_MODEL,
            "input": [
                {
                    "role": "system",
                    "content": company_prompt,
                },
                {
                    "role": "system",
                    "content": grounded_prompt,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        user_payload,
                        ensure_ascii=False,
                    ),
                },
            ],
            "text": {
                "format": {
                    "type": "json_object"
                }
            },
        }

        headers = {
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=40) as client:
            response = client.post(
                config.OPENAI_API_URL,
                headers=headers,
                json=payload,
            )

        if response.status_code >= 400:
            raise RuntimeError(
                f"OpenAI error {response.status_code}: {response.text}"
            )

        data = response.json()
        output_text = self.extract_output_text(data)

        if not output_text:
            raise RuntimeError("OpenAI returned empty output")

        return json.loads(output_text)

    def normalize_result(
        self,
        result: dict[str, Any],
        company_id: int | None = None,
    ) -> dict[str, Any]:
        # Compared case-insensitively against the company's own codes, which are
        # normalised to lowercase ascii on the way in. The model is shown those
        # codes and usually returns one verbatim; a returned "Sales" that is
        # discarded as unrecognised is a routing decision thrown away over
        # capitalisation.
        department = str(
            result.get("department") or "unknown"
        ).strip()

        allowed = self.allowed_departments(company_id)
        matched = next(
            (
                code
                for code in allowed
                if code.lower() == department.lower()
            ),
            None,
        )

        department = matched or "unknown"

        language = str(
            result.get("language") or "ar"
        ).strip().lower()

        if language not in ["ar", "en"]:
            language = "ar"

        try:
            confidence = float(result.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        confidence = max(0.0, min(confidence, 1.0))

        reply = str(result.get("reply") or "").strip()

        if not reply:
            reply = self.safe_reply(language)

        buttons = self.clean_string_list(
            result.get("buttons"),
            limit=5,
            item_limit=24,
        )

        missing_information = self.clean_string_list(
            result.get("missing_information"),
            limit=10,
            item_limit=100,
        )

        used_knowledge_ids = self.clean_string_list(
            result.get("used_knowledge_ids"),
            limit=10,
            item_limit=100,
        )

        return {
            "department": department,
            "intent": str(
                result.get("intent") or "unknown"
            ).strip(),
            "topic": str(
                result.get("topic") or department
            ).strip(),
            "language": language,
            "confidence": confidence,
            "reply": reply,
            "buttons": buttons,
            "needs_human": bool(
                result.get("needs_human", False)
            ),
            "missing_information": missing_information,
            "used_knowledge_ids": used_knowledge_ids,
            "notes": str(
                result.get("notes") or ""
            ).strip(),
        }

    def apply_guardrails(
        self,
        result: dict[str, Any],
        message: str,
        knowledge: list[dict],
        connector_results: list[dict],
    ) -> dict[str, Any]:
        language = result.get("language") or "ar"
        lowered_message = message.lower()

        asks_price = self.contains_any(
            lowered_message,
            [
                "price",
                "how much",
                "cost",
                "سعر",
                "قديش",
                "بكم",
                "كم حق",
                "$",
            ],
        )

        asks_stock = self.contains_any(
            lowered_message,
            [
                "available",
                "in stock",
                "stock",
                "متوفر",
                "موجود",
                "عندكم",
            ],
        )

        asks_balance = self.contains_any(
            lowered_message,
            [
                "balance",
                "what do i owe",
                "شو علي",
                "شو عليي",
                "حسابي",
            ],
        )

        has_connector_fact = any(
            item.get("ok") is True
            for item in connector_results
            if isinstance(item, dict)
        )

        if asks_balance and not has_connector_fact:
            result["needs_human"] = True
            result["missing_information"].append(
                "verified customer identity and accounting data"
            )

        if (asks_price or asks_stock) and not has_connector_fact:
            result["needs_human"] = True

        if not knowledge and not connector_results:
            result["needs_human"] = True

            if not result.get("missing_information"):
                result["missing_information"] = [
                    "verified business information"
                ]

        reply = result.get("reply") or ""

        if not has_connector_fact and self.looks_like_unverified_price(reply):
            result["reply"] = self.unverified_price_reply(language)
            result["needs_human"] = True
            result["notes"] = (
                result.get("notes", "")
                + " Price guardrail replaced an unverified price."
            ).strip()

        if result.get("needs_human"):
            support_label = (
                "التواصل مع الدعم"
                if language == "ar"
                else "Contact support"
            )

            if support_label not in result["buttons"]:
                result["buttons"].append(support_label)

        return result

    def safe_reply(self, language: str) -> str:
        if language == "en":
            return (
                "I do not have enough confirmed information to answer that accurately. "
                "Please send a little more detail, or our team can check it for you."
            )

        return (
            "ما عندي معلومات مؤكدة كافية حتى جاوبك بدقة. "
            "ابعتلنا تفاصيل أكتر، أو فينا نحولك للفريق ليتأكدلك."
        )

    def unverified_price_reply(self, language: str) -> str:
        if language == "en":
            return (
                "I cannot confirm the current price from the available information. "
                "Please send the exact model and storage, and our team will verify it."
            )

        return (
            "ما فيني أكد السعر الحالي من المعلومات المتاحة. "
            "ابعتلنا الموديل والسعة بالتحديد، والفريق بيتأكدلك."
        )

    def clean_string_list(
        self,
        value: Any,
        limit: int,
        item_limit: int,
    ) -> list[str]:
        if not isinstance(value, list):
            return []

        cleaned = []

        for item in value:
            text = str(item).strip()

            if text and text not in cleaned:
                cleaned.append(text[:item_limit])

        return cleaned[:limit]

    def contains_any(
        self,
        text: str,
        phrases: list[str],
    ) -> bool:
        return any(phrase in text for phrase in phrases)

    def looks_like_unverified_price(self, reply: str) -> bool:
        patterns = [
            r"\$\s?\d+",
            r"\d+\s?\$",
            r"\d+\s?(usd|دولار)",
        ]

        return any(
            re.search(pattern, reply, re.IGNORECASE)
            for pattern in patterns
        )

    def extract_output_text(
        self,
        data: dict[str, Any],
    ) -> str | None:
        if data.get("output_text"):
            return data["output_text"]

        for output_item in data.get("output", []):
            for content in output_item.get("content", []):
                if content.get("type") in ["output_text", "text"]:
                    return content.get("text")

        return None


ai_router = AIRouter()