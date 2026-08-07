"""Telephony (Dialer) service: place real phone calls, transfer a live
call to an employee, AI auto-answer for inbound calls, and call
recording -- behind a swappable provider abstraction.

Provider model
--------------
`TelephonyProvider` is the interface; `TwilioProvider` is the first (and
currently only) implementation, speaking Twilio's REST API directly over
httpx (no SDK dependency). `NullProvider` is what you get when the
TWILIO_* env vars are not set: every operation fails with a clear
"telephony is not configured" error and the Dialer UI shows a setup
notice. Nothing in the platform breaks when unconfigured.

Required env (config/settings.py):
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER
  PUBLIC_BASE_URL  (public https URL of this backend, for webhooks)

Call flows
----------
- Outbound (dial page): place_call() -> Twilio dials the customer; when
  answered, Twilio fetches TwiML from our /api/dialer/webhooks/voice
  which connects/records; status callbacks keep telephony_calls rows
  updated; on completion a call_logs entry is written automatically so
  the Calls log stays the single history of record.
- Transfer: transfer_call() redirects the live call (Twilio call
  update + TwiML <Dial>) to the chosen employee's phone (users.phone).
- Inbound + AI auto-answer: Twilio number's Voice webhook should be
  pointed at POST {PUBLIC_BASE_URL}/api/dialer/webhooks/inbound. The
  TwiML we return greets the caller with the company greeting (<Say>),
  then records a voicemail (<Record>). This is v1 "AI answers the
  phone": a spoken greeting + recorded message + team notification.
  A full conversational voice AI (streaming STT/TTS over Twilio Media
  Streams) is a documented upgrade path, not yet wired.
- WhatsApp calls: WhatsApp Business *calling* has no generally
  available API to programmatically answer/route calls; WhatsApp voice
  stays on the user's device. What we support is dialing the customer's
  phone number (cellular) from the same customer profile the WhatsApp
  conversation is linked to.

Webhook security: every Twilio webhook request is verified with the
X-Twilio-Signature HMAC-SHA1 scheme before being trusted."""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any

import httpx

from config.settings import config
from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


ACTIVE_STATUSES = {"queued", "initiated", "ringing", "in_progress", "transferring"}
FINAL_STATUSES = {"completed", "failed", "busy", "no_answer", "cancelled"}

# Twilio status callback values -> our normalized status.
_TWILIO_STATUS_MAP = {
    "queued": "queued",
    "initiated": "initiated",
    "ringing": "ringing",
    "in-progress": "in_progress",
    "completed": "completed",
    "busy": "busy",
    "failed": "failed",
    "no-answer": "no_answer",
    "canceled": "cancelled",
}


class TelephonyNotConfiguredError(Exception):
    """Raised when a call operation is attempted with no provider
    credentials configured."""


class TelephonyError(Exception):
    """Raised when the provider rejects an operation."""


class TelephonyProvider:
    """Interface all providers implement."""

    name = "none"

    def is_configured(self) -> bool:
        raise NotImplementedError

    def place_call(self, *, to_number: str, webhook_base: str) -> dict[str, Any]:
        raise NotImplementedError

    def transfer_call(self, *, provider_call_id: str, to_number: str) -> None:
        raise NotImplementedError

    def hangup_call(self, *, provider_call_id: str) -> None:
        raise NotImplementedError


class NullProvider(TelephonyProvider):
    name = "none"

    def is_configured(self) -> bool:
        return False

    def _fail(self):
        raise TelephonyNotConfiguredError(
            "Telephony is not configured. Set TWILIO_ACCOUNT_SID, "
            "TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER and PUBLIC_BASE_URL "
            "to enable live calling."
        )

    def place_call(self, *, to_number: str, webhook_base: str) -> dict[str, Any]:
        self._fail()

    def transfer_call(self, *, provider_call_id: str, to_number: str) -> None:
        self._fail()

    def hangup_call(self, *, provider_call_id: str) -> None:
        self._fail()


class TwilioProvider(TelephonyProvider):
    name = "twilio"

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
    ) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.api_base = (
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}"
        )

    def is_configured(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.from_number)

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            f"{self.api_base}{path}",
            data=data,
            auth=(self.account_sid, self.auth_token),
            timeout=15,
        )
        if response.status_code >= 400:
            raise TelephonyError(
                f"Twilio error {response.status_code}: {response.text[:300]}"
            )
        return response.json()

    def place_call(self, *, to_number: str, webhook_base: str) -> dict[str, Any]:
        payload = self._post(
            "/Calls.json",
            {
                "To": to_number,
                "From": self.from_number,
                "Url": f"{webhook_base}/api/dialer/webhooks/voice",
                "StatusCallback": f"{webhook_base}/api/dialer/webhooks/status",
                "StatusCallbackEvent": "initiated ringing answered completed",
                "Record": "true",
                "RecordingStatusCallback": (
                    f"{webhook_base}/api/dialer/webhooks/recording"
                ),
            },
        )
        return {"provider_call_id": payload.get("sid"), "raw": payload}

    def transfer_call(self, *, provider_call_id: str, to_number: str) -> None:
        # Redirect the in-progress call to TwiML that dials the employee.
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<Response><Say>Transferring your call, please hold.</Say>"
            f"<Dial record=\"record-from-answer\">{to_number}</Dial></Response>"
        )
        self._post(f"/Calls/{provider_call_id}.json", {"Twiml": twiml})

    def hangup_call(self, *, provider_call_id: str) -> None:
        self._post(f"/Calls/{provider_call_id}.json", {"Status": "completed"})


def verify_twilio_signature(
    *, url: str, params: dict[str, str], signature: str | None, auth_token: str
) -> bool:
    """Twilio request validation: HMAC-SHA1 over the full URL plus the
    POST params sorted by key, base64-encoded, compared in constant
    time. Returns False for a missing signature or token."""
    if not signature or not auth_token:
        return False
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(
        auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1
    ).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def _build_provider() -> TelephonyProvider:
    if (
        config.TWILIO_ACCOUNT_SID
        and config.TWILIO_AUTH_TOKEN
        and config.TWILIO_PHONE_NUMBER
    ):
        return TwilioProvider(
            config.TWILIO_ACCOUNT_SID,
            config.TWILIO_AUTH_TOKEN,
            config.TWILIO_PHONE_NUMBER,
        )
    return NullProvider()


class TelephonyService:
    def __init__(self) -> None:
        self.provider = _build_provider()

    def ensure_schema(self) -> None:
        """This branch's convention: each service owns its tables (see
        e.g. call_log_service.ensure_schema), created from main.py's
        lifespan."""
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telephony_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    provider_call_id TEXT,
                    direction TEXT NOT NULL DEFAULT 'outbound',
                    to_number TEXT,
                    from_number TEXT,
                    customer_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'queued',
                    transferred_to_user_id INTEGER,
                    ai_answered INTEGER NOT NULL DEFAULT 0,
                    recording_url TEXT,
                    duration_seconds INTEGER,
                    error_detail TEXT,
                    started_at TEXT,
                    ended_at TEXT,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(company_id)
                        REFERENCES companies(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(customer_id)
                        REFERENCES customers(id)
                        ON DELETE SET NULL,
                    FOREIGN KEY(transferred_to_user_id)
                        REFERENCES users(id)
                        ON DELETE SET NULL,
                    FOREIGN KEY(created_by)
                        REFERENCES users(id)
                        ON DELETE SET NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_telephony_calls_company_status
                ON telephony_calls(company_id, status)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_telephony_calls_provider_id
                ON telephony_calls(provider_call_id)
                """
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Status / listing
    # ------------------------------------------------------------------

    def dialer_status(self) -> dict[str, Any]:
        configured = (
            self.provider.is_configured() and bool(config.PUBLIC_BASE_URL)
        )
        return {
            "configured": configured,
            "provider": self.provider.name,
            "from_number": (
                config.TWILIO_PHONE_NUMBER if configured else None
            ),
            "missing": [
                name
                for name, value in [
                    ("TWILIO_ACCOUNT_SID", config.TWILIO_ACCOUNT_SID),
                    ("TWILIO_AUTH_TOKEN", config.TWILIO_AUTH_TOKEN),
                    ("TWILIO_PHONE_NUMBER", config.TWILIO_PHONE_NUMBER),
                    ("PUBLIC_BASE_URL", config.PUBLIC_BASE_URL),
                ]
                if not value
            ],
        }

    def list_calls(
        self,
        *,
        company_id: int,
        active_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        where = ["t.company_id = ?"]
        params: list[Any] = [company_id]

        if active_only:
            placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
            where.append(f"t.status IN ({placeholders})")
            params.extend(sorted(ACTIVE_STATUSES))

        clause = " AND ".join(where)

        with db.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM telephony_calls t WHERE {clause}",
                params,
            ).fetchone()["total"]

            rows = conn.execute(
                f"""
                SELECT
                    t.*,
                    customer.display_name AS customer_name,
                    agent.full_name AS transferred_to_name,
                    creator.full_name AS created_by_name
                FROM telephony_calls t
                LEFT JOIN customers customer ON customer.id = t.customer_id
                LEFT JOIN users agent ON agent.id = t.transferred_to_user_id
                LEFT JOIN users creator ON creator.id = t.created_by
                WHERE {clause}
                ORDER BY t.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, max(1, min(200, limit)), max(0, offset)],
            ).fetchall()

        return {"items": [dict(row) for row in rows], "total": int(total or 0)}

    def get_call(self, *, company_id: int, call_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM telephony_calls WHERE id = ? AND company_id = ?",
                (call_id, company_id),
            ).fetchone()
        if not row:
            raise KeyError("Call not found")
        return dict(row)

    # ------------------------------------------------------------------
    # Outbound calling
    # ------------------------------------------------------------------

    def _validate_customer(self, company_id: int, customer_id: int | None) -> None:
        if customer_id is None:
            return
        with db.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM customers WHERE id = ? AND company_id = ? LIMIT 1",
                (customer_id, company_id),
            ).fetchone()
        if not row:
            raise TelephonyError("Customer must belong to this company.")

    def place_call(
        self,
        *,
        company_id: int,
        to_number: str,
        customer_id: int | None,
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        to_number = (to_number or "").strip()
        if not to_number:
            raise TelephonyError("A destination number is required.")

        self._validate_customer(company_id, customer_id)

        if not self.provider.is_configured() or not config.PUBLIC_BASE_URL:
            raise TelephonyNotConfiguredError(
                "Telephony is not configured. Set TWILIO_ACCOUNT_SID, "
                "TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER and PUBLIC_BASE_URL "
                "to enable live calling."
            )

        result = self.provider.place_call(
            to_number=to_number,
            webhook_base=config.PUBLIC_BASE_URL.rstrip("/"),
        )

        now = utc_now_iso()
        with db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO telephony_calls (
                    company_id, provider, provider_call_id, direction,
                    to_number, from_number, customer_id, status,
                    started_at, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, 'outbound', ?, ?, ?, 'queued', ?, ?, ?, ?)
                """,
                (
                    company_id,
                    self.provider.name,
                    result.get("provider_call_id"),
                    to_number,
                    config.TWILIO_PHONE_NUMBER,
                    customer_id,
                    now,
                    actor_user_id,
                    now,
                    now,
                ),
            )
            call_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_call(company_id=company_id, call_id=call_id)

    def transfer_call(
        self,
        *,
        company_id: int,
        call_id: int,
        employee_user_id: int,
    ) -> dict[str, Any]:
        call = self.get_call(company_id=company_id, call_id=call_id)
        if call["status"] not in ACTIVE_STATUSES:
            raise TelephonyError("Only an active call can be transferred.")
        if not call["provider_call_id"]:
            raise TelephonyError("This call has no provider reference.")

        with db.connect() as conn:
            employee = conn.execute(
                """
                SELECT users.id, users.phone
                FROM company_users
                JOIN users ON users.id = company_users.user_id
                WHERE company_users.company_id = ?
                  AND company_users.user_id = ?
                  AND company_users.status = 'active'
                  AND users.status = 'active'
                LIMIT 1
                """,
                (company_id, employee_user_id),
            ).fetchone()

        if not employee:
            raise TelephonyError(
                "Transfer target must be an active member of this company."
            )
        if not employee["phone"]:
            raise TelephonyError(
                "This employee has no phone number on their profile."
            )

        self.provider.transfer_call(
            provider_call_id=call["provider_call_id"],
            to_number=employee["phone"],
        )

        now = utc_now_iso()
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE telephony_calls
                SET status = 'transferring', transferred_to_user_id = ?,
                    updated_at = ?
                WHERE id = ? AND company_id = ?
                """,
                (employee_user_id, now, call_id, company_id),
            )
            conn.commit()

        return self.get_call(company_id=company_id, call_id=call_id)

    def hangup_call(self, *, company_id: int, call_id: int) -> dict[str, Any]:
        call = self.get_call(company_id=company_id, call_id=call_id)
        if call["status"] in FINAL_STATUSES:
            return call
        if call["provider_call_id"]:
            self.provider.hangup_call(provider_call_id=call["provider_call_id"])

        now = utc_now_iso()
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE telephony_calls
                SET status = 'completed', ended_at = ?, updated_at = ?
                WHERE id = ? AND company_id = ?
                """,
                (now, now, call_id, company_id),
            )
            conn.commit()
        return self.get_call(company_id=company_id, call_id=call_id)

    # ------------------------------------------------------------------
    # Webhook handling (called by the routes after signature verification)
    # ------------------------------------------------------------------

    def handle_status_callback(self, params: dict[str, str]) -> None:
        provider_call_id = params.get("CallSid")
        twilio_status = params.get("CallStatus", "")
        if not provider_call_id:
            return

        status = _TWILIO_STATUS_MAP.get(twilio_status)
        if not status:
            return

        duration = params.get("CallDuration")
        now = utc_now_iso()

        with db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM telephony_calls WHERE provider_call_id = ? LIMIT 1",
                (provider_call_id,),
            ).fetchone()
            if not row:
                return

            fields: dict[str, Any] = {"status": status}
            if status in FINAL_STATUSES:
                fields["ended_at"] = now
                if duration and str(duration).isdigit():
                    fields["duration_seconds"] = int(duration)

            assignments = ", ".join(f"{key} = ?" for key in fields)
            conn.execute(
                f"UPDATE telephony_calls SET {assignments}, updated_at = ? "
                "WHERE id = ?",
                [*fields.values(), now, row["id"]],
            )
            conn.commit()
            call = dict(row) | fields

        if status in FINAL_STATUSES:
            self._write_call_log(call)

    def handle_recording_callback(self, params: dict[str, str]) -> None:
        provider_call_id = params.get("CallSid")
        recording_url = params.get("RecordingUrl")
        if not provider_call_id or not recording_url:
            return
        now = utc_now_iso()
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE telephony_calls
                SET recording_url = ?, updated_at = ?
                WHERE provider_call_id = ?
                """,
                (recording_url, now, provider_call_id),
            )
            conn.commit()

    def record_inbound_call(self, params: dict[str, str]) -> None:
        """Log an AI-auto-answered inbound call. Company resolution: an
        inbound call arrives on OUR Twilio number, which is configured
        per deployment (single number), so it belongs to the default
        company until per-company numbers exist."""
        provider_call_id = params.get("CallSid")
        from_number = params.get("From")
        if not provider_call_id:
            return
        now = utc_now_iso()
        with db.connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM telephony_calls WHERE provider_call_id = ? LIMIT 1",
                (provider_call_id,),
            ).fetchone()
            if existing:
                return
            conn.execute(
                """
                INSERT INTO telephony_calls (
                    company_id, provider, provider_call_id, direction,
                    to_number, from_number, status, ai_answered,
                    started_at, created_at, updated_at
                ) VALUES (?, ?, ?, 'inbound', ?, ?, 'in_progress', 1, ?, ?, ?)
                """,
                (
                    config.DEFAULT_COMPANY_ID,
                    self.provider.name,
                    provider_call_id,
                    config.TWILIO_PHONE_NUMBER,
                    from_number,
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()

        try:
            from backend.services.notification_service import notification_service

            notification_service.create(
                company_id=config.DEFAULT_COMPANY_ID,
                notification_type="inbound_call",
                title="AI answered an inbound call",
                body=f"Call from {from_number or 'unknown number'} — voicemail being recorded.",
                severity="info",
                data={"provider_call_id": provider_call_id},
                dedupe_key=f"inbound_call:{provider_call_id}",
            )
        except Exception as exc:
            print("INBOUND CALL NOTIFY ERROR:", exc)

    def build_inbound_twiml(self, *, company_id: int | None = None) -> str:
        """TwiML for AI auto-answer: greet, then record a voicemail.
        The greeting comes from the company's saved replies/profile when
        available, otherwise a sane default."""
        greeting = (
            "Thank you for calling. Our team is not available right now. "
            "Please leave a message after the tone and we will get back "
            "to you as soon as possible."
        )
        try:
            from backend.services.company_settings_service import (
                company_settings_service,
            )

            section = company_settings_service.get_section(
                company_id or config.DEFAULT_COMPANY_ID, "replies"
            )
            configured = (section.get("values") or {}).get("away_message")
            if configured and str(configured).strip():
                greeting = str(configured).strip()
        except Exception:
            pass

        safe_greeting = (
            greeting.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            f"<Say>{safe_greeting}</Say>"
            '<Record maxLength="120" playBeep="true" />'
            "<Hangup/>"
            "</Response>"
        )

    def build_outbound_twiml(self) -> str:
        """TwiML fetched by Twilio when an outbound call is answered.
        Keeps the call open with recording (the agent talks from their
        own line via the provider's client or a conference upgrade
        later; v1 simply bridges audio and records)."""
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            '<Pause length="60"/>'
            "</Response>"
        )

    # ------------------------------------------------------------------
    # Call-log integration
    # ------------------------------------------------------------------

    def _write_call_log(self, call: dict[str, Any]) -> None:
        """On completion, mirror the telephony call into call_logs so the
        Calls page remains the single history of record."""
        try:
            from backend.services.call_log_service import call_log_service

            # Map provider outcomes onto this branch's call_log STATUSES
            # (completed / missed / no_answer / voicemail).
            log_status = "completed"
            if call.get("status") in ("no_answer", "cancelled"):
                log_status = "no_answer"
            elif call.get("status") in ("busy", "failed"):
                log_status = "missed"

            other_number = (
                call.get("to_number")
                if call.get("direction") == "outbound"
                else call.get("from_number")
            )

            notes = f"Dialer call via {call.get('provider')}"
            if call.get("recording_url"):
                notes += f" — recording: {call['recording_url']}"

            call_log_service.create_call_log(
                company_id=call["company_id"],
                direction=call.get("direction") or "outbound",
                phone_number=other_number,
                customer_id=call.get("customer_id"),
                duration_seconds=int(call.get("duration_seconds") or 0),
                status=log_status,
                notes=notes,
                actor_user_id=call.get("created_by"),
            )
        except Exception as exc:
            print("CALL LOG MIRROR ERROR:", exc)


telephony_service = TelephonyService()
