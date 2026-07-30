from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx

from config.settings import config
from database.database import db
from core.instruction_service import instruction_service


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# The manager is teaching the AI how to behave, in a private training
# chat — not a customer conversation. Keep this prompt narrowly scoped
# to that one job (acknowledge + optionally extract one instruction) so
# it stays cheap, fast, and doesn't drift into open-ended chit-chat.
TEACHING_SYSTEM_PROMPT = """
You are being taught how to behave by this company's manager, in a private
training chat (the manager is not a customer — never treat their messages
as customer requests to fulfill). Your job on every message:
1. Reply briefly and naturally, acknowledging what they said.
2. If — and only if — their message contains an actual behavioral
   instruction or fact you should remember for future customer
   conversations, extract it as ONE concise, self-contained, imperative
   sentence (e.g. "Always greet customers in Arabic first" or "Mention
   the 10% loyalty discount when asked about pricing"). If they're just
   chatting, asking a question, or gave no new instruction, this must be
   null — never invent an instruction that wasn't actually given.
Respond ONLY as JSON: {"reply": "<short acknowledgment>", "instruction": "<extracted instruction, or null>"}.
""".strip()


class AITeachingChatService:
    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_teaching_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    instruction_saved INTEGER NOT NULL DEFAULT 0,
                    actor_user_id INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
                )
                """
            )
            conn.commit()

    def list_messages(self, *, company_id: int) -> list[dict[str, Any]]:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ai_teaching_messages WHERE company_id = ? ORDER BY id",
                (company_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _save_message(
        self, conn, *, company_id: int, role: str, text: str,
        instruction_saved: bool = False, actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        cursor = conn.execute(
            """
            INSERT INTO ai_teaching_messages (company_id, role, text, instruction_saved, actor_user_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (company_id, role, text, int(instruction_saved), actor_user_id, now),
        )
        return {
            "id": int(cursor.lastrowid),
            "company_id": company_id,
            "role": role,
            "text": text,
            "instruction_saved": instruction_saved,
            "actor_user_id": actor_user_id,
            "created_at": now,
        }

    def send_message(self, *, company_id: int, actor_user_id: int | None, text: str) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("Message text is required.")
        if not config.OPENAI_API_KEY:
            raise RuntimeError("AI Teaching Chat isn't configured yet (missing OPENAI_API_KEY).")

        with db.connect() as conn:
            manager_message = self._save_message(
                conn, company_id=company_id, role="manager", text=text, actor_user_id=actor_user_id,
            )
            conn.commit()

        reply_text, instruction_text, error = self._ask_openai(text)

        instruction_saved = False
        if instruction_text:
            try:
                instruction_service.create(
                    company_id=company_id, text=instruction_text, tags=[], actor_user_id=actor_user_id,
                )
                instruction_saved = True
            except ValueError:
                instruction_saved = False

        with db.connect() as conn:
            assistant_message = self._save_message(
                conn, company_id=company_id, role="assistant", text=reply_text,
                instruction_saved=instruction_saved,
            )
            conn.commit()

        return {
            "manager_message": manager_message,
            "assistant_message": assistant_message,
            "instruction_saved": instruction_saved,
            "instruction_text": instruction_text if instruction_saved else None,
            "error": error,
        }

    def _ask_openai(self, text: str) -> tuple[str, str | None, str | None]:
        """Returns (reply_text, extracted_instruction_or_None, error_or_None).
        Never raises — a failed/misbehaving AI call degrades to a plain
        acknowledgment rather than breaking the teaching chat."""
        payload = {
            "model": config.OPENAI_MODEL,
            "input": [
                {"role": "system", "content": TEACHING_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "text": {"format": {"type": "json_object"}},
        }
        headers = {
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
        try:
            response = self._post_to_openai(payload, headers)
            if response.status_code >= 400:
                raise RuntimeError(f"OpenAI error {response.status_code}: {response.text}")
            data = response.json()
            output_text = self._extract_output_text(data)
            if not output_text:
                raise RuntimeError("OpenAI returned empty output")
            parsed = json.loads(output_text)
        except Exception as exc:
            return "Sorry, I couldn't process that right now.", None, str(exc)

        reply_text = str(parsed.get("reply") or "Got it.")
        instruction_text = parsed.get("instruction")
        instruction_text = (
            instruction_text.strip()
            if isinstance(instruction_text, str) and instruction_text.strip()
            else None
        )
        return reply_text, instruction_text, None

    @staticmethod
    def _post_to_openai(payload: dict[str, Any], headers: dict[str, str]):
        """Isolated so tests can mock this one method without patching
        httpx.Client globally (which would also break FastAPI's
        TestClient, since it's httpx-based too)."""
        with httpx.Client(timeout=40) as client:
            return client.post(config.OPENAI_API_URL, headers=headers, json=payload)

    @staticmethod
    def _extract_output_text(data: dict[str, Any]) -> str | None:
        if data.get("output_text"):
            return data["output_text"]
        for output_item in data.get("output", []):
            for content in output_item.get("content", []):
                if content.get("type") in ["output_text", "text"]:
                    return content.get("text")
        return None


ai_teaching_chat_service = AITeachingChatService()
