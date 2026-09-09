"""Turn a plain-language description into a Reply Flow's steps.

The builder's "Write it" mode hands us a few lines -- "greet them, ask what they
need, let the AI answer from the knowledge base, hand off if they ask for a
human" -- and expects back a laid-out graph of the same nodes the canvas draws.
This asks the model for a constrained JSON step list, maps it onto the node
vocabulary the builder and engine share, lays the steps out in a vertical chain,
and stores it.

It fails soft: with no model configured, or on any model error, it returns
None. The route turns that into a clear "build it on the canvas instead"
message rather than a broken screen -- the AI is a convenience here, never the
only way to build a flow.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from backend.services.reply_flow_service import (
    DEFAULT_TRIGGER,
    NODE_TYPES,
    TRIGGER_KEYS,
    reply_flow_service,
)
from config.settings import config


logger = logging.getLogger(__name__)

# What the model is allowed to emit, spelled out so it cannot invent a step the
# canvas cannot draw or the engine cannot run.
_NODE_MENU = """
Available step types (use the exact key on the left):
- greeting: opening message. config: {"text": "..."}
- company_intro: short business intro. config: {"text": "..."}
- ask_question: ask something and save the answer. config: {"question": "...", "save_as": "variable_name"}
- ai_direct: AI answers freely. config: {"instructions": "..."}
- ai_knowledge_only: AI answers only from the knowledge base. config: {"instructions": "..."}
- ai_knowledge_plus: AI uses the knowledge base then reasons. config: {"instructions": "..."}
- canned_reply: send fixed text exactly. config: {"text": "..."}
- human_handoff: stop AI and notify a person. config: {"note": "..."}
- appointment: collect details to book an appointment. config: {"note": "..."}
- create_task: create an internal task. config: {"task_type": "follow_up|complaint|service_request|sales_inquiry|internal|other", "note": "..."}
- product_suggest: suggest from the catalogue. config: {"note": "..."}
- condition: branch on a saved answer. config: {"variable": "...", "operator": "equals|contains|greater_than|less_than|is_set", "value": "..."}
- timeout_followup: follow up if the customer goes quiet. config: {"wait_minutes": 60, "text": "..."}
- close_chat: wrap up and close. config: {"ask_reschedule": false}
- end: end the flow.
""".strip()


def _layout(steps: list[dict[str, Any]]) -> dict[str, list]:
    """A vertical chain: each step below the last, connected in order."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        node_type = str(step.get("node_type") or step.get("nodeType") or "").strip()
        if node_type not in NODE_TYPES:
            continue
        config_data = step.get("config")
        node_id = f"gen-{index + 1}"
        nodes.append(
            {
                "id": node_id,
                "type": "step",
                "position": {"x": 240, "y": 80 + index * 140},
                "data": {
                    "nodeType": node_type,
                    "label": str(step.get("label") or "").strip(),
                    "config": config_data if isinstance(config_data, dict) else {},
                },
            }
        )
        if len(nodes) >= 2:
            edges.append(
                {
                    "id": f"edge-{len(nodes) - 1}",
                    "source": nodes[-2]["id"],
                    "target": nodes[-1]["id"],
                }
            )
    return {"nodes": nodes, "edges": edges}


class ReplyFlowGenerator:
    def _ask_model(self, text: str) -> dict[str, Any] | None:
        if not config.AI_ENABLED or not config.OPENAI_API_KEY:
            return None

        system = (
            "You convert a business owner's plain description of a chat flow "
            "into a strict JSON plan. Return ONLY JSON of the form "
            '{"trigger_type": "<one of the allowed triggers>", '
            '"trigger_config": {}, "steps": [{"node_type": "...", '
            '"label": "short internal name", "config": {...}}]}. '
            "Use only the step keys and config shapes given. Keep it to the "
            "steps the description actually asks for, in order. Do not invent "
            "products, prices or facts inside the text.\n\n"
            f"{_NODE_MENU}\n\n"
            "Allowed triggers: " + ", ".join(sorted(TRIGGER_KEYS)) + "."
        )

        payload = {
            "model": config.OPENAI_MODEL,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "text": {"format": {"type": "json_object"}},
        }
        headers = {
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=40) as client:
                response = client.post(config.OPENAI_API_URL, headers=headers, json=payload)
            if response.status_code >= 400:
                logger.warning("Flow generator model error %s", response.status_code)
                return None
            data = response.json()
        except Exception:  # noqa: BLE001
            logger.exception("Flow generator request failed")
            return None

        # Responses API: the text lives under output[].content[].text.
        output_text = ""
        for item in data.get("output", []):
            for part in item.get("content", []):
                if part.get("type") in ("output_text", "text") and part.get("text"):
                    output_text = part["text"]
                    break
            if output_text:
                break

        if not output_text:
            return None
        try:
            return json.loads(output_text)
        except (TypeError, ValueError):
            logger.warning("Flow generator returned non-JSON")
            return None

    def generate(
        self, *, company_id: int, flow_id: int, text: str
    ) -> dict[str, Any] | None:
        parsed = self._ask_model(text)
        if not parsed or not isinstance(parsed.get("steps"), list):
            return None

        graph = _layout(parsed["steps"])
        if not graph["nodes"]:
            return None

        trigger_type = str(parsed.get("trigger_type") or "").strip()
        trigger_config = parsed.get("trigger_config")
        if trigger_type not in TRIGGER_KEYS:
            trigger_type = DEFAULT_TRIGGER
            trigger_config = {}

        return reply_flow_service.set_graph(
            company_id=company_id,
            flow_id=flow_id,
            nodes=graph["nodes"],
            edges=graph["edges"],
            trigger_type=trigger_type,
            trigger_config=trigger_config if isinstance(trigger_config, dict) else {},
        )


reply_flow_generator = ReplyFlowGenerator()
