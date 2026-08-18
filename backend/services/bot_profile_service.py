"""The teaching profile: how one company's assistant is told to speak.

Everything the AI TEACHING screen edits lives in ``bot_profiles`` inside the
company's own encrypted database. That placement is the whole point of the
module. The prompt used to be composed from ``config/bot_profile.json`` — one
file, shared by every company on the platform — so a tone or an instruction
written by one owner was spoken to every other company's customers, and no
company could describe itself without overwriting somebody else.

Two behaviours here are deliberate rather than incidental:

* A company that has never opened the screen still has a profile. The first
  read creates one with working defaults instead of returning nothing, because
  an empty screen and an unteachable assistant are the same bug to the person
  looking at it.
* ``preview_reply`` runs the real assistant with no side effect at all. It is
  the only way an owner can see what the bot will say *before* a customer does.

Table creation belongs to ``database/schema_tenant.py`` alone; nothing here
issues DDL.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from database.manager import database_manager
from backend.services.channel_account_service import channel_account_service
from backend.services.plan_service import plan_service


logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BotProfileError(RuntimeError):
    """A profile change that was refused, with a reason worth showing."""


# Offered by the screen as a starting point. Free text is still accepted: a
# clinic and a phone shop do not sound alike, and a fixed list would force one
# of them to lie about itself.
SUGGESTED_TONES = (
    "friendly",
    "professional",
    "formal",
    "casual",
    "concise",
    "warm",
    "technical",
)

SUPPORTED_LANGUAGES = ("ar", "en")

DEFAULT_SYSTEM_PROMPT = (
    "You are the customer service assistant for this business.\n"
    "- Answer only from the company's confirmed knowledge.\n"
    "- If something is not confirmed, say so and offer to check with the team.\n"
    "- Never invent prices, stock, offers or delivery dates.\n"
    "- Keep replies short, clear and polite."
)

DEFAULT_WELCOME_AR = "أهلاً وسهلاً! كيف فينا نساعدك اليوم؟"
DEFAULT_WELCOME_EN = "Welcome! How can we help you today?"

MAX_NAME = 120
MAX_TONE = 60
MAX_SYSTEM_PROMPT = 6000
MAX_WELCOME = 1000
MAX_EXAMPLES = 20
MAX_EXAMPLE_FIELD = 1000
MAX_MODEL = 80

ALLOWED_STATUS = ("active", "disabled")

# Channels the dry run may impersonate. Anything else would be answered by a
# policy this platform does not have, so the preview would be a lie.
PREVIEW_CHANNELS = (
    "messenger",
    "instagram",
    "whatsapp",
    "telegram",
    "website_chat",
)

DRY_RUN_USER_PREFIX = "ai-teaching-dry-run"

_PUBLIC_COLUMNS = (
    "id",
    "company_id",
    "channel_account_id",
    "name",
    "default_language",
    "tone",
    "system_prompt",
    "welcome_enabled",
    "welcome_message_ar",
    "welcome_message_en",
    "examples_json",
    "ai_enabled",
    "ai_model",
    "memory_enabled",
    "human_handover_enabled",
    "is_default",
    "status",
    "created_at",
    "updated_at",
)

_BOOLEAN_COLUMNS = (
    "welcome_enabled",
    "ai_enabled",
    "memory_enabled",
    "human_handover_enabled",
    "is_default",
)


class BotProfileService:
    def __init__(self) -> None:
        # Guards the lazy creation of a company's first profile. Two requests
        # arriving together would otherwise both see "no default" and insert
        # one each, leaving the screen editing a row the assistant does not
        # read.
        self._create_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def get_default(self, company_id: int) -> dict[str, Any]:
        """The company's default profile, created on first read if missing."""
        company_id = int(company_id)

        existing = self._default_row(company_id)

        if existing:
            return self._public(existing)

        with self._create_lock:
            # Re-check inside the lock: another request may have created it
            # while this one waited.
            existing = self._default_row(company_id)

            if existing:
                return self._public(existing)

            self._insert(
                company_id=company_id,
                values={
                    "name": "Default Assistant",
                    "default_language": "ar",
                    "tone": "friendly",
                    "system_prompt": DEFAULT_SYSTEM_PROMPT,
                    "welcome_enabled": True,
                    "welcome_message_ar": DEFAULT_WELCOME_AR,
                    "welcome_message_en": DEFAULT_WELCOME_EN,
                    "examples": [],
                },
                is_default=True,
            )

        row = self._default_row(company_id)

        if not row:
            raise BotProfileError("The default assistant profile could not be created.")

        return self._public(row)

    def list_profiles(self, company_id: int) -> list[dict[str, Any]]:
        company_id = int(company_id)

        # Reading the default first guarantees the list is never empty, which
        # is what the screen and the assistant both assume.
        self.get_default(company_id)

        with database_manager.tenant(company_id) as conn:
            rows = conn.execute(
                """
                SELECT * FROM bot_profiles
                WHERE company_id = ?
                ORDER BY is_default DESC, id ASC
                """,
                (company_id,),
            ).fetchall()

        return [self._public(row) for row in rows]

    def get_profile(self, company_id: int, profile_id: int) -> dict[str, Any] | None:
        row = self._row(int(company_id), int(profile_id))

        return self._public(row) if row else None

    def resolve_profile(
        self,
        company_id: int,
        channel_account_id: int | None = None,
    ) -> dict[str, Any]:
        """The profile that answers for this channel account.

        An account with its own active profile uses it; everything else falls
        back to the company default. The fallback is what keeps a newly
        connected page answering at all.
        """
        company_id = int(company_id)

        if channel_account_id:
            with database_manager.tenant(company_id) as conn:
                row = conn.execute(
                    """
                    SELECT * FROM bot_profiles
                    WHERE company_id = ?
                      AND channel_account_id = ?
                      AND status = 'active'
                    ORDER BY id ASC
                    LIMIT 1
                    """,
                    (company_id, int(channel_account_id)),
                ).fetchone()

            if row:
                return self._public(row)

        return self.get_default(company_id)

    def prompt_profile(
        self,
        company_id: int | None,
        channel_account_id: int | None = None,
    ) -> dict[str, Any] | None:
        """The profile for prompt building — never raises.

        This runs on the customer reply path. A company database that will not
        open must not take the reply down with it: the caller falls back to a
        neutral prompt, which is a worse answer but still an answer.
        """
        if not company_id:
            return None

        try:
            return self.resolve_profile(int(company_id), channel_account_id)
        except Exception:
            logger.exception(
                "Could not load the assistant profile for company %s", company_id
            )
            return None

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def create_profile(
        self,
        *,
        company_id: int,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        """Create an additional profile, usually bound to one channel account."""
        company_id = int(company_id)

        # A company always has exactly one default; extra profiles are never it.
        self.get_default(company_id)

        cleaned = self._clean_values(company_id=company_id, values=values, creating=True)

        if not cleaned.get("name"):
            raise BotProfileError("Give the profile a name.")

        profile_id = self._insert(
            company_id=company_id,
            values=cleaned,
            is_default=False,
        )

        row = self._row(company_id, profile_id)

        return self._public(row)

    def update_profile(
        self,
        *,
        company_id: int,
        profile_id: int,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        """Change one profile. Ownership is checked on every write.

        Profile ids are small integers and therefore guessable, so the company
        is part of the WHERE clause rather than trusted from the caller.
        """
        company_id = int(company_id)
        profile_id = int(profile_id)

        row = self._row(company_id, profile_id)

        if not row:
            raise BotProfileError("This assistant profile does not exist.")

        cleaned = self._clean_values(
            company_id=company_id,
            values=values,
            creating=False,
        )

        if bool(row["is_default"]) and "channel_account_id" in cleaned:
            # The default answers for everything that has no profile of its
            # own; pinning it to one account would silently leave every other
            # account unteachable.
            raise BotProfileError(
                "The default profile answers for every channel and cannot be "
                "bound to a single account."
            )

        if not cleaned:
            return self._public(row)

        assignments = ", ".join(f"{column} = ?" for column in cleaned)
        parameters = [*cleaned.values(), utc_now_iso(), profile_id, company_id]

        with database_manager.tenant(company_id) as conn:
            conn.execute(
                f"""
                UPDATE bot_profiles
                SET {assignments}, updated_at = ?
                WHERE id = ? AND company_id = ?
                """,
                parameters,
            )
            conn.commit()

        return self._public(self._row(company_id, profile_id))

    def update_default(
        self,
        *,
        company_id: int,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        """What the screen's Save button calls."""
        default_profile = self.get_default(company_id)

        return self.update_profile(
            company_id=company_id,
            profile_id=int(default_profile["id"]),
            values=values,
        )

    def delete_profile(self, company_id: int, profile_id: int) -> bool:
        company_id = int(company_id)
        profile_id = int(profile_id)

        row = self._row(company_id, profile_id)

        if not row:
            return False

        if bool(row["is_default"]):
            raise BotProfileError(
                "The default profile cannot be deleted; it is what the "
                "assistant falls back to."
            )

        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                "DELETE FROM bot_profiles WHERE id = ? AND company_id = ?",
                (profile_id, company_id),
            )
            conn.commit()

        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Dry run
    # ------------------------------------------------------------------

    @staticmethod
    def _assert_preview_budget(company_id: int) -> None:
        """Refuse a preview once this company has had its month's worth.

        A hard platform cap, not a plan allowance — see `preview_reply` for
        why the two are different things. Fails **open** on a counter that
        cannot be read: a usage table that is unavailable must not take the
        tuning screen away from every company on the platform. The cost of
        being wrong in that direction is some untracked model calls during an
        outage; the cost of being wrong in the other is everybody's assistant
        untestable because one query failed.
        """
        from config.settings import config

        cap = int(getattr(config, "AI_PREVIEW_MAX_PER_PERIOD", 0) or 0)

        if cap <= 0:
            return

        try:
            used = int(
                plan_service.usage_total(
                    company_id=company_id, metric=plan_service.AI_PREVIEW_METRIC
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Could not read the preview counter for company %s", company_id
            )
            return

        if used >= cap:
            raise BotProfileError(
                f"This month's {cap} assistant previews have been used. The "
                "counter resets at the start of next month; the assistant "
                "itself keeps answering customers normally."
            )

    def preview_reply(
        self,
        *,
        company_id: int,
        message: str,
        channel: str = "messenger",
        language: str | None = None,
    ) -> dict[str, Any]:
        """Run the real assistant for this company and return what it would say.

        This is the reason the screen is worth having: an owner can read the
        reply before a customer can. It is therefore only useful if it is
        genuinely the real pipeline, and only safe if it leaves nothing behind.

        Isolation, and why each part holds:

        * **Nothing is sent to a channel provider.** Sending lives above this
          layer — ``channels/meta/smart_reply.py`` and the ``sender`` modules
          call the gateway and then deliver the result. Entering at
          ``message_gateway.handle_text`` computes a reply and returns it; no
          code below that point can reach a provider API.
        * **No message is stored.** Message rows are written by
          ``channels/inbound.py`` and ``smart_reply`` — again above the
          gateway. The engine itself writes to the company database in exactly
          one place, ``create_ticket``, reached only from a flow state that
          collects input. A fresh session starts in the flow's opening state,
          and that this state collects no input is checked below rather than
          assumed, so a later change to the flow fails loudly here instead of
          quietly filing tickets from previews.
        * **No reply is queued.** ``pending_replies`` rows are enqueued by
          ``smart_reply``, which is not on this path.
        * **No conversation state is touched.** The engine's only state is the
          in-memory session store, keyed by the user id it is given. The dry
          run passes a unique synthetic id that no real customer can collide
          with, and deletes that entry afterwards, so a live conversation's
          language, department and history are left exactly as they were.

        The model call itself is not suppressed — the point is to see the real
        reply. With ``AI_ENABLED`` off or no API key, ``ai_router.route``
        returns ``None`` and the engine answers with its safe fallback, which
        is also exactly what a customer would receive in that configuration.

        Because the model call is real, it is **counted and capped**. It was
        neither: ``plan_service.record_usage`` had exactly one caller in the
        repository — the live reply path — so a preview spent the operator's
        model budget and moved no number anybody looks at. Anyone holding
        ``settings.manage`` could script this endpoint and the only evidence
        would be the invoice.

        The cap is a hard platform limit rather than a plan allowance, on
        purpose. A plan limit is the operator's commercial decision about a
        customer; this is the platform refusing to let one account spend
        without bound, which is not something a bigger plan should buy. It is
        also set two orders of magnitude above real use, so somebody genuinely
        tuning their assistant never meets it.

        The counter is written under its own metric. Folding previews into
        ``ai_replies`` would make every usage screen and every invoice count
        tests as customer conversations.
        """
        company_id = int(company_id)
        message = (message or "").strip()

        if not message:
            raise BotProfileError("Type a customer message to test.")

        channel = (channel or "messenger").strip().lower()

        if channel not in PREVIEW_CHANNELS:
            raise BotProfileError(
                "Test one of: " + ", ".join(PREVIEW_CHANNELS) + "."
            )

        if language not in (None, "", *SUPPORTED_LANGUAGES):
            raise BotProfileError("Language must be 'ar' or 'en'.")

        # Imported here, not at module scope: ``core.prompt_builder`` imports
        # this service, so a top-level import would close a cycle.
        from config.settings import config
        from core.automation_policy import automation_policy
        from core.flow_loader import flow_loader
        from core.prompt_builder import company_scope
        from core.session import session as session_store
        from gateway.message_gateway import message_gateway

        self._assert_start_state_writes_nothing(session_store, flow_loader)

        self._assert_preview_budget(company_id)

        profile = self.resolve_profile(company_id)

        # Unique per run and namespaced, so it cannot be the key of any real
        # conversation even for the instant it exists.
        preview_user_id = f"{DRY_RUN_USER_PREFIX}:{company_id}:{uuid4().hex}"

        try:
            with company_scope(company_id):
                response = message_gateway.handle_text(
                    channel=channel,
                    user_id=preview_user_id,
                    company_id=company_id,
                    message=message,
                    language=language or None,
                )
        finally:
            self._drop_preview_session(session_store, preview_user_id)

        # After the call, not before: a preview that failed cost nothing and
        # should not be billed. Same reasoning as the live path, which counts
        # only after the provider accepted the reply.
        plan_service.record_usage(
            company_id=company_id,
            metric=plan_service.AI_PREVIEW_METRIC,
            channel=channel,
        )

        model_available = bool(
            getattr(config, "AI_ENABLED", False)
            and getattr(config, "OPENAI_API_KEY", "")
        )
        # This company's own answer, not the platform's. Without the company
        # the preview would tell an owner their channel is on the scripted path
        # when they have set it to answer with the assistant.
        ai_path = automation_policy.should_auto_reply_with_ai(
            channel, company_id=company_id
        )

        if not ai_path:
            note = (
                f"The '{channel}' channel is not set to auto-reply with AI, so "
                "this is the scripted flow answer a customer would get."
            )
        elif not model_available:
            note = (
                "No AI model is configured, so the assistant answered with its "
                "safe fallback — which is what a customer would receive right now."
            )
        else:
            note = "Generated by the live assistant. Nothing was sent or saved."

        return {
            "reply": getattr(response, "text", "") or "",
            "buttons": list(getattr(response, "buttons", []) or []),
            "channel": channel,
            "profile_id": profile.get("id"),
            "profile_name": profile.get("name"),
            "ai_path": ai_path,
            "model_available": model_available,
            "note": note,
            "delivered": False,
            "stored": False,
        }

    @staticmethod
    def _assert_start_state_writes_nothing(session_store: Any, flow_loader: Any) -> None:
        """Refuse to preview if the flow's opening state collects input.

        An input state is the one path where the engine writes to the company
        database (it can file a ticket). Today the opening state has no input,
        so a preview cannot reach that write — this check keeps that true after
        somebody edits the flow, instead of letting previews start filing
        tickets silently.
        """
        start_state = session_store.default_session().get("state")
        state_data = flow_loader.get_state(start_state) or {}

        if "input" in state_data:
            raise BotProfileError(
                "The assistant's opening step now collects input, so a test "
                "message could create a real record. Testing is disabled until "
                "that is reviewed."
            )

    @staticmethod
    def _drop_preview_session(session_store: Any, preview_user_id: str) -> None:
        """Remove the throwaway session, whatever happened during the run."""
        try:
            with session_store._lock:  # noqa: SLF001 - no public delete exists
                session_store.sessions.pop(preview_user_id, None)
                session_store._touched.pop(preview_user_id, None)  # noqa: SLF001
        except Exception:
            logger.exception("Could not clear the dry-run session")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _default_row(self, company_id: int):
        with database_manager.tenant(int(company_id)) as conn:
            return conn.execute(
                """
                SELECT * FROM bot_profiles
                WHERE company_id = ? AND is_default = 1
                ORDER BY id ASC
                LIMIT 1
                """,
                (int(company_id),),
            ).fetchone()

    def _row(self, company_id: int, profile_id: int):
        with database_manager.tenant(int(company_id)) as conn:
            return conn.execute(
                "SELECT * FROM bot_profiles WHERE id = ? AND company_id = ? LIMIT 1",
                (int(profile_id), int(company_id)),
            ).fetchone()

    def _insert(
        self,
        *,
        company_id: int,
        values: dict[str, Any],
        is_default: bool,
    ) -> int:
        now = utc_now_iso()

        examples = values.get("examples")

        if "examples_json" in values:
            examples_json = values["examples_json"]
        else:
            examples_json = json.dumps(
                self._clean_examples(examples or []), ensure_ascii=False
            )

        with database_manager.tenant(int(company_id)) as conn:
            cursor = conn.execute(
                """
                INSERT INTO bot_profiles (
                    company_id, channel_account_id, name, default_language,
                    tone, system_prompt, welcome_enabled, welcome_message_ar,
                    welcome_message_en, examples_json, ai_enabled, ai_model,
                    memory_enabled, human_handover_enabled, is_default,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(company_id),
                    values.get("channel_account_id"),
                    values.get("name") or "Default Assistant",
                    values.get("default_language") or "ar",
                    values.get("tone") or "friendly",
                    values.get("system_prompt") or DEFAULT_SYSTEM_PROMPT,
                    1 if values.get("welcome_enabled", True) else 0,
                    values.get("welcome_message_ar", DEFAULT_WELCOME_AR),
                    values.get("welcome_message_en", DEFAULT_WELCOME_EN),
                    examples_json,
                    1 if values.get("ai_enabled", True) else 0,
                    values.get("ai_model"),
                    1 if values.get("memory_enabled", True) else 0,
                    1 if values.get("human_handover_enabled", True) else 0,
                    1 if is_default else 0,
                    values.get("status") or "active",
                    now,
                    now,
                ),
            )
            conn.commit()

            return int(cursor.lastrowid)

    def _clean_values(
        self,
        *,
        company_id: int,
        values: dict[str, Any],
        creating: bool,
    ) -> dict[str, Any]:
        """Whitelist and validate one update.

        Only the columns the screen owns can be written. Passing the payload
        straight through would let a caller flip ``company_id`` or ``is_default``
        and take over another company's assistant.
        """
        cleaned: dict[str, Any] = {}

        if "name" in values:
            cleaned["name"] = self._text(values["name"], MAX_NAME, "Name")

        if "default_language" in values:
            language = str(values["default_language"] or "").strip().lower()

            if language not in SUPPORTED_LANGUAGES:
                raise BotProfileError("Default language must be 'ar' or 'en'.")

            cleaned["default_language"] = language

        if "tone" in values:
            cleaned["tone"] = self._text(values["tone"], MAX_TONE, "Tone", allow_empty=True)

        if "system_prompt" in values:
            cleaned["system_prompt"] = self._text(
                values["system_prompt"],
                MAX_SYSTEM_PROMPT,
                "System instructions",
                allow_empty=True,
            )

        if "welcome_message_ar" in values:
            cleaned["welcome_message_ar"] = self._text(
                values["welcome_message_ar"], MAX_WELCOME, "Arabic welcome", allow_empty=True
            )

        if "welcome_message_en" in values:
            cleaned["welcome_message_en"] = self._text(
                values["welcome_message_en"], MAX_WELCOME, "English welcome", allow_empty=True
            )

        if "ai_model" in values:
            cleaned["ai_model"] = self._text(
                values["ai_model"], MAX_MODEL, "Model", allow_empty=True
            )

        for flag in (
            "welcome_enabled",
            "ai_enabled",
            "memory_enabled",
            "human_handover_enabled",
        ):
            if flag in values and values[flag] is not None:
                cleaned[flag] = 1 if bool(values[flag]) else 0

        if "status" in values and values["status"] is not None:
            status_value = str(values["status"]).strip().lower()

            if status_value not in ALLOWED_STATUS:
                raise BotProfileError(
                    "Status must be one of: " + ", ".join(ALLOWED_STATUS) + "."
                )

            cleaned["status"] = status_value

        if "examples" in values and values["examples"] is not None:
            cleaned["examples_json"] = json.dumps(
                self._clean_examples(values["examples"]), ensure_ascii=False
            )

        if "channel_account_id" in values:
            account_id = values["channel_account_id"]

            if account_id in (None, "", 0):
                if not creating:
                    cleaned["channel_account_id"] = None
            else:
                cleaned["channel_account_id"] = self._verified_account_id(
                    company_id, account_id
                )

        return cleaned

    @staticmethod
    def _verified_account_id(company_id: int, account_id: Any) -> int:
        """Refuse to bind a profile to an account this company does not own.

        Without this check a company could point its assistant at another
        company's connected page id, and later screens would read the two as
        related.
        """

        try:
            account_id = int(account_id)
        except (TypeError, ValueError) as exc:
            raise BotProfileError("Channel account is not valid.") from exc

        account = channel_account_service.get_account(int(company_id), account_id)

        if not account:
            raise BotProfileError("That channel account does not belong to this company.")

        return account_id

    @staticmethod
    def _text(
        value: Any,
        limit: int,
        label: str,
        allow_empty: bool = False,
    ) -> str | None:
        if value is None:
            if allow_empty:
                return None

            raise BotProfileError(f"{label} is required.")

        text = str(value).strip()

        if not text:
            if allow_empty:
                return None

            raise BotProfileError(f"{label} is required.")

        if len(text) > limit:
            raise BotProfileError(f"{label} must be {limit} characters or fewer.")

        return text

    @classmethod
    def _clean_examples(cls, value: Any) -> list[dict[str, str]]:
        """Normalise the taught examples.

        Examples are serialized into the system prompt on every customer
        message, so an unbounded list is both a cost problem and a way to push
        the real instructions out of the model's attention.
        """
        if value in (None, ""):
            return []

        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise BotProfileError("Examples are not valid JSON.") from exc

        if not isinstance(value, list):
            raise BotProfileError("Examples must be a list of customer/reply pairs.")

        if len(value) > MAX_EXAMPLES:
            raise BotProfileError(f"Keep {MAX_EXAMPLES} examples or fewer.")

        cleaned: list[dict[str, str]] = []

        for item in value:
            if not isinstance(item, dict):
                raise BotProfileError(
                    "Each example needs a customer message and a reply."
                )

            customer = str(
                item.get("customer") or item.get("question") or ""
            ).strip()
            reply = str(item.get("reply") or item.get("answer") or "").strip()

            if not customer or not reply:
                raise BotProfileError(
                    "Each example needs both a customer message and a reply."
                )

            if len(customer) > MAX_EXAMPLE_FIELD or len(reply) > MAX_EXAMPLE_FIELD:
                raise BotProfileError(
                    f"Each part of an example must be {MAX_EXAMPLE_FIELD} "
                    "characters or fewer."
                )

            cleaned.append({"customer": customer, "reply": reply})

        return cleaned

    @classmethod
    def _public(cls, row: Any) -> dict[str, Any]:
        data = {key: row[key] for key in _PUBLIC_COLUMNS}

        for flag in _BOOLEAN_COLUMNS:
            data[flag] = bool(data.get(flag))

        raw_examples = data.pop("examples_json", "[]")

        try:
            parsed = json.loads(raw_examples or "[]")
        except (json.JSONDecodeError, TypeError):
            parsed = []

        data["examples"] = parsed if isinstance(parsed, list) else []

        return data


bot_profile_service = BotProfileService()
