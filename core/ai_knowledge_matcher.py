import json
import logging
from typing import Any

import httpx

from config.settings import config


logger = logging.getLogger(__name__)


class AIKnowledgeMatcher:
    """
    Uses AI to semantically select the most relevant knowledge entries.

    It does not write the customer reply.
    It only selects which knowledge items should be given to the reply AI.
    """

    def match(
        self,
        message: str,
        language: str,
        items: list[dict],
        context: dict[str, Any] | None = None,
        max_results: int = 3,
    ) -> dict[str, Any]:
        if not message or not items:
            return self.empty_result()

        if not config.AI_ENABLED or not config.OPENAI_API_KEY:
            return self.empty_result()

        payload = {
            "model": config.OPENAI_MODEL,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "You are a semantic knowledge selector for a business assistant.\n\n"
                        "Your job is only to select relevant knowledge items.\n"
                        "Do not answer the customer.\n\n"
                        "Rules:\n"
                        "- Match by meaning, not exact keywords.\n"
                        "- Understand Arabic, English, Arabizi, slang, and spelling mistakes.\n"
                        "- The current message has priority over previous context.\n"
                        "- Use context only when the message is clearly a follow-up.\n"
                        "- Never force the previous topic onto a new topic.\n"
                        "- Select no more than the requested maximum.\n"
                        "- If the knowledge does not contain the requested information, "
                        "return matched=false.\n"
                        "- Return JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "message": message,
                            "language": language,
                            "conversation_context": context or {},
                            "knowledge_items": items,
                            "max_results": max_results,
                            "required_output": {
                                "matched": True,
                                "confidence": 0.0,
                                "department": "sales",
                                "topic": "phones",
                                "selected_ids": [
                                    "knowledge_item_id"
                                ],
                                "is_follow_up": False,
                                "reason": "Short explanation"
                            },
                        },
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

        try:
            with httpx.Client(timeout=30) as client:
                response = client.post(
                    config.OPENAI_API_URL,
                    headers=headers,
                    json=payload,
                )

            if response.status_code >= 400:
                logger.error(
                    "Knowledge matcher OpenAI error %s: %s",
                    response.status_code,
                    response.text,
                )
                return self.empty_result()

            data = response.json()
            output_text = self.extract_output_text(data)

            if not output_text:
                logger.warning("Knowledge matcher returned empty output")
                return self.empty_result()

            raw_result = json.loads(output_text)
            return self.normalize_result(raw_result, items, max_results)

        except Exception:
            logger.exception("AI knowledge matcher failed")
            return self.empty_result()

    def normalize_result(
        self,
        result: dict[str, Any],
        available_items: list[dict],
        max_results: int,
    ) -> dict[str, Any]:
        available_ids = {
            str(item.get("id"))
            for item in available_items
            if item.get("id")
        }

        selected_ids = result.get("selected_ids") or []

        if not isinstance(selected_ids, list):
            selected_ids = []

        clean_ids = []

        for item_id in selected_ids:
            value = str(item_id).strip()

            if value in available_ids and value not in clean_ids:
                clean_ids.append(value)

        clean_ids = clean_ids[:max_results]

        try:
            confidence = float(result.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        confidence = max(0.0, min(confidence, 1.0))

        matched = bool(result.get("matched")) and bool(clean_ids)

        return {
            "matched": matched,
            "confidence": confidence,
            "department": str(
                result.get("department") or "unknown"
            ).strip(),
            "topic": str(
                result.get("topic") or "unknown"
            ).strip(),
            "selected_ids": clean_ids,
            "is_follow_up": bool(result.get("is_follow_up", False)),
            "reason": str(result.get("reason") or "").strip(),
        }

    def select_items(
        self,
        match_result: dict[str, Any],
        items: list[dict],
    ) -> list[dict]:
        selected_ids = match_result.get("selected_ids") or []

        if not selected_ids:
            return []

        item_map = {
            str(item.get("id")): item
            for item in items
            if item.get("id")
        }

        selected = []

        for item_id in selected_ids:
            item = item_map.get(str(item_id))

            if item:
                selected.append(item)

        return selected

    def extract_output_text(self, data: dict[str, Any]) -> str | None:
        if data.get("output_text"):
            return data["output_text"]

        for output_item in data.get("output", []):
            for content in output_item.get("content", []):
                if content.get("type") in ["output_text", "text"]:
                    return content.get("text")

        return None

    def empty_result(self) -> dict[str, Any]:
        return {
            "matched": False,
            "confidence": 0.0,
            "department": "unknown",
            "topic": "unknown",
            "selected_ids": [],
            "is_follow_up": False,
            "reason": "",
        }


ai_knowledge_matcher = AIKnowledgeMatcher()