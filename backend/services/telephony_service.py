"""Telephony — the Dialer: place a call, transfer it, hang it up, and answer
inbound calls with a recorded greeting, behind a provider the platform can swap.

Provider model
--------------
`TelephonyProvider` is the interface. `TwilioProvider` is the only
implementation, speaking Twilio's REST API over httpx rather than pulling in an
SDK. `NullProvider` is what the platform runs on when `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER` and `PUBLIC_BASE_URL` are not all
set: every call operation is refused with a message that names what is missing,
the Dialer screen shows a setup notice instead of a dial pad, and nothing else
in the platform changes. That is the state a fresh install is in, and it has to
be a clean one — a company that has not bought a phone line has not broken
anything.

What lives where
----------------
`telephony_calls` is the provider's view of a call while it is happening: its
call id, the status it reports, the recording it produces. `call_logs` is the
company's history. When a call reaches a final status the finished call is
written into `call_logs` through `call_log_service`, so the Calls screen stays
the single history of record and never learns the provider's vocabulary.

Webhooks and the company they belong to
---------------------------------------
Every company is its own encrypted database, and a provider callback names only
the provider's call id — so there is no company to open until one is found.
`_locate_call` asks each active company's database in turn. That is one small
indexed lookup per active company, once per callback (not per second, and not
per request an employee makes), and it is the price of the isolation: there is
no cross-company table to query because there is no cross-company file.

An inbound call is the harder case and is deliberately left narrow. The number
belongs to the deployment, not to a company, so the only safe attribution is
`database_manager.default_company_id()` — which answers with a company only when
the platform serves exactly one. With several companies the call is still
answered and still recorded by the provider; it simply is not filed against a
company, because guessing would file one company's caller into another
company's records. Per-company numbers are what would fix that, and they are a
change to how numbers are bought and stored, not something this module can
invent.

Security: every webhook request is verified with Twilio's X-Twilio-Signature
scheme before anything is read from it. With no auth token configured nothing
can be verified, so everything is rejected.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Any

import httpx

from config.settings import config
from database.manager import database_manager, utc_now_iso


logger = logging.getLogger(__name__)


ACTIVE_STATUSES: frozenset[str] = frozenset(
    {"queued", "initiated", "ringing", "in_progress", "transferring"}
)
FINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "busy", "no_answer", "cancelled"}
)

# Twilio's status vocabulary mapped onto ours. A value not in this map is not a
# status we understand and is ignored rather than stored — an unrecognised
# string in `status` would sit outside both ACTIVE and FINAL and leave the call
# neither live nor finished.
_TWILIO_STATUS = {
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

# How a finished provider call is recorded in the company's history. The Calls
# log has four outcomes and the provider has six; this is the whole translation.
_LOG_STATUS = {
    "completed": "completed",
    "no_answer": "no_answer",
    "cancelled": "no_answer",
    "busy": "missed",
    "failed": "missed",
}

NOT_CONFIGURED_MESSAGE = (
    "Telephony is not configured. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
    "TWILIO_PHONE_NUMBER and PUBLIC_BASE_URL to enable live calling."
)

# What an inbound caller hears when the platform answers. Fixed wording: the
# design this was rebuilt from read a per-company "away message" from a settings
# section that does not exist in this codebase, and inventing one would be a
# setting nobody can find and nobody set.
INBOUND_GREETING = (
    "Thank you for calling. Our team is not available right now. "
    "Please leave a message after the tone and we will get back to you "
    "as soon as possible."
)

MAX_RECORDING_SECONDS = 120


class TelephonyNotConfiguredError(Exception):
    """A call operation was attempted with no provider credentials."""


class TelephonyError(Exception):
    """The provider, or this company's own data, refused the operation."""


class CallNotFound(Exception):
    """No such call in this company's dialer history."""


# ----------------------------------------------------------------------
# Providers
# ----------------------------------------------------------------------


class TelephonyProvider:
    """What a provider has to be able to do."""

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
    """No credentials, so no calls — and a clear refusal rather than a crash."""

    name = "none"

    def is_configured(self) -> bool:
        return False

    def _refuse(self) -> None:
        raise TelephonyNotConfiguredError(NOT_CONFIGURED_MESSAGE)

    def place_call(self, *, to_number: str, webhook_base: str) -> dict[str, Any]:
        self._refuse()

    def transfer_call(self, *, provider_call_id: str, to_number: str) -> None:
        self._refuse()

    def hangup_call(self, *, provider_call_id: str) -> None:
        self._refuse()


class TwilioProvider(TelephonyProvider):
    name = "twilio"

    def __init__(
        self, account_sid: str, auth_token: str, from_number: str
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
        try:
            response = httpx.post(
                f"{self.api_base}{path}",
                data=data,
                auth=(self.account_sid, self.auth_token),
                timeout=15,
            )
        except httpx.HTTPError as exc:
            raise TelephonyError(
                "The telephony provider could not be reached."
            ) from exc

        if response.status_code >= 400:
            # The provider's own words, truncated: it is the only place the
            # reason lives ("this number is unverified", "insufficient funds"),
            # and hiding it would leave an employee with a call that failed and
            # no way to find out why.
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

        return {"provider_call_id": payload.get("sid")}

    def transfer_call(self, *, provider_call_id: str, to_number: str) -> None:
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            "<Say>Transferring your call, please hold.</Say>"
            f'<Dial record="record-from-answer">{_escape(to_number)}</Dial>'
            "</Response>"
        )
        self._post(f"/Calls/{provider_call_id}.json", {"Twiml": twiml})

    def hangup_call(self, *, provider_call_id: str) -> None:
        self._post(
            f"/Calls/{provider_call_id}.json", {"Status": "completed"}
        )


def _escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def verify_twilio_signature(
    *,
    url: str,
    params: dict[str, str],
    signature: str | None,
    auth_token: str,
) -> bool:
    """Twilio's request validation, in full.

    HMAC-SHA1 over the full URL followed by every POST parameter, sorted by
    name, key and value concatenated with nothing between them; base64; compared
    in constant time. A missing signature or a missing token is False — with
    nothing to verify against, nothing is verified, and the webhook endpoints
    reject everything rather than trusting a request they cannot check.
    """
    if not signature or not auth_token:
        return False

    payload = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(
        auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1
    ).digest()

    return hmac.compare_digest(base64.b64encode(digest).decode("utf-8"), signature)


def build_provider() -> TelephonyProvider:
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


# ----------------------------------------------------------------------
# The service
# ----------------------------------------------------------------------


class TelephonyService:
    def __init__(self) -> None:
        self.provider = build_provider()

    # ------------------------------------------------------------------
    # State the screen reads
    # ------------------------------------------------------------------

    def dialer_status(self) -> dict[str, Any]:
        """Whether calling works here, and what is missing when it does not.

        The list of missing names is the whole point: "not configured" sends an
        administrator looking through documentation, and "PUBLIC_BASE_URL"
        sends them to one line of one file.
        """
        missing = [
            name
            for name, value in (
                ("TWILIO_ACCOUNT_SID", config.TWILIO_ACCOUNT_SID),
                ("TWILIO_AUTH_TOKEN", config.TWILIO_AUTH_TOKEN),
                ("TWILIO_PHONE_NUMBER", config.TWILIO_PHONE_NUMBER),
                ("PUBLIC_BASE_URL", config.PUBLIC_BASE_URL),
            )
            if not value
        ]
        configured = not missing and self.provider.is_configured()

        return {
            "configured": configured,
            "provider": self.provider.name,
            "from_number": config.TWILIO_PHONE_NUMBER if configured else None,
            "missing": missing,
        }

    def list_calls(
        self,
        *,
        company_id: int,
        active_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        company_id = int(company_id)
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))

        where = ["telephony_calls.company_id = ?"]
        params: list[Any] = [company_id]

        if active_only:
            placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
            where.append(f"telephony_calls.status IN ({placeholders})")
            params.extend(sorted(ACTIVE_STATUSES))

        clause = " AND ".join(where)

        with database_manager.tenant(company_id) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS total FROM telephony_calls WHERE {clause}",
                params,
            ).fetchone()["total"]

            rows = conn.execute(
                f"""
                SELECT telephony_calls.*,
                       COALESCE(customers.display_name, customers.internal_name)
                           AS customer_name
                FROM telephony_calls
                LEFT JOIN customers
                    ON customers.id = telephony_calls.customer_id
                WHERE {clause}
                ORDER BY telephony_calls.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        return {"items": [dict(row) for row in rows], "total": int(total or 0)}

    def get_call(self, *, company_id: int, call_id: int) -> dict[str, Any]:
        with database_manager.tenant(int(company_id)) as conn:
            row = conn.execute(
                """
                SELECT * FROM telephony_calls
                WHERE id = ? AND company_id = ?
                """,
                (int(call_id), int(company_id)),
            ).fetchone()

        if not row:
            raise CallNotFound("Call not found")

        return dict(row)

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    def place_call(
        self,
        *,
        company_id: int,
        to_number: str,
        customer_id: int | None,
        actor_user_id: int | None,
    ) -> dict[str, Any]:
        """Dial a number, and record the attempt only once the provider has it.

        The order matters. The provider is asked first and the row is written
        second, so a refused call leaves no half-created record claiming a call
        is ringing that nobody is on.
        """
        company_id = int(company_id)
        to_number = (to_number or "").strip()

        if not to_number:
            raise TelephonyError("A destination number is required.")

        self._assert_customer(company_id, customer_id)
        self._assert_configured()

        result = self.provider.place_call(
            to_number=to_number,
            webhook_base=config.PUBLIC_BASE_URL.rstrip("/"),
        )
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            cursor = conn.execute(
                """
                INSERT INTO telephony_calls (
                    company_id, provider, provider_call_id, direction,
                    to_number, from_number, customer_id, status, ai_answered,
                    started_at, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, 'outbound', ?, ?, ?, 'queued', 0, ?, ?, ?, ?)
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
        self, *, company_id: int, call_id: int, employee_user_id: int
    ) -> dict[str, Any]:
        company_id = int(company_id)
        call = self.get_call(company_id=company_id, call_id=call_id)

        if call["status"] not in ACTIVE_STATUSES:
            raise TelephonyError("Only a call that is still live can be transferred.")

        if not call["provider_call_id"]:
            raise TelephonyError("This call has no provider reference to redirect.")

        phone = self._employee_phone(company_id, int(employee_user_id))
        self._assert_configured()

        self.provider.transfer_call(
            provider_call_id=call["provider_call_id"], to_number=phone
        )

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            conn.execute(
                """
                UPDATE telephony_calls
                SET status = 'transferring',
                    transferred_to_user_id = ?,
                    updated_at = ?
                WHERE id = ? AND company_id = ?
                """,
                (int(employee_user_id), now, int(call_id), company_id),
            )
            conn.commit()

        return self.get_call(company_id=company_id, call_id=call_id)

    def hangup_call(self, *, company_id: int, call_id: int) -> dict[str, Any]:
        company_id = int(company_id)
        call = self.get_call(company_id=company_id, call_id=call_id)

        # Already over. Ending it again is not an error — the button was
        # pressed twice, or a status callback arrived first.
        if call["status"] in FINAL_STATUSES:
            return call

        if call["provider_call_id"]:
            self._assert_configured()
            self.provider.hangup_call(provider_call_id=call["provider_call_id"])

        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
            conn.execute(
                """
                UPDATE telephony_calls
                SET status = 'completed', ended_at = ?, updated_at = ?
                WHERE id = ? AND company_id = ?
                """,
                (now, now, int(call_id), company_id),
            )
            conn.commit()

        # No history entry written here. The provider sends a status callback
        # for a call it has ended, and that is where the finished call is
        # mirrored into `call_logs` — writing it in both places would record
        # every hung-up call twice.
        return self.get_call(company_id=company_id, call_id=call_id)

    # ------------------------------------------------------------------
    # Provider callbacks
    # ------------------------------------------------------------------

    def handle_status_callback(self, params: dict[str, str]) -> None:
        provider_call_id = params.get("CallSid")
        status = _TWILIO_STATUS.get(params.get("CallStatus", ""))

        if not provider_call_id or not status:
            return

        located = self._locate_call(provider_call_id)

        if not located:
            return

        company_id, call = located
        duration = params.get("CallDuration")
        now = utc_now_iso()

        fields: dict[str, Any] = {"status": status}

        if status in FINAL_STATUSES:
            fields["ended_at"] = now

            if duration and str(duration).isdigit():
                fields["duration_seconds"] = int(duration)

        assignments = ", ".join(f"{key} = ?" for key in fields)

        with database_manager.tenant(company_id) as conn:
            conn.execute(
                f"UPDATE telephony_calls SET {assignments}, updated_at = ? "
                "WHERE id = ? AND company_id = ?",
                [*fields.values(), now, int(call["id"]), company_id],
            )
            conn.commit()

        if status in FINAL_STATUSES:
            self._write_call_log(company_id, {**call, **fields})

    def handle_recording_callback(self, params: dict[str, str]) -> None:
        provider_call_id = params.get("CallSid")
        recording_url = params.get("RecordingUrl")

        if not provider_call_id or not recording_url:
            return

        located = self._locate_call(provider_call_id)

        if not located:
            return

        company_id, call = located

        with database_manager.tenant(company_id) as conn:
            conn.execute(
                """
                UPDATE telephony_calls
                SET recording_url = ?, updated_at = ?
                WHERE id = ? AND company_id = ?
                """,
                (recording_url, utc_now_iso(), int(call["id"]), company_id),
            )
            conn.commit()

    def record_inbound_call(self, params: dict[str, str]) -> None:
        """File an inbound call the platform answered.

        Only when the platform serves exactly one company. See this module's
        docstring: one deployment-wide number cannot be attributed to one of
        several tenants, and filing it against the wrong one would put a
        stranger's phone number in another company's records.
        """
        provider_call_id = params.get("CallSid")

        if not provider_call_id:
            return

        company_id = database_manager.default_company_id()

        if company_id is None:
            logger.warning(
                "Inbound call %s answered but not filed: the platform serves "
                "several companies and the number belongs to none of them.",
                provider_call_id,
            )
            return

        from_number = params.get("From")
        now = utc_now_iso()

        with database_manager.tenant(company_id) as conn:
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
                    company_id,
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

        self._notify_inbound(company_id, provider_call_id, from_number)

    # ------------------------------------------------------------------
    # TwiML
    # ------------------------------------------------------------------

    def build_outbound_twiml(self) -> str:
        """What the provider plays when the person we dialled picks up.

        A pause holds the line open while the call is recorded. This is the
        honest limit of v1: the audio is bridged and recorded, and a two-way
        browser conversation needs a client SDK or a conference, which is a
        further piece of work rather than a line of TwiML.
        """
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response><Pause length=\"60\"/></Response>"
        )

    def build_inbound_twiml(self) -> str:
        """What a caller hears when the platform answers: a greeting, then a
        recorded message."""
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            f"<Say>{_escape(INBOUND_GREETING)}</Say>"
            f'<Record maxLength="{MAX_RECORDING_SECONDS}" playBeep="true" />'
            "<Hangup/>"
            "</Response>"
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _assert_configured(self) -> None:
        if not self.provider.is_configured() or not config.PUBLIC_BASE_URL:
            raise TelephonyNotConfiguredError(NOT_CONFIGURED_MESSAGE)

    @staticmethod
    def _assert_customer(company_id: int, customer_id: int | None) -> None:
        if customer_id is None:
            return

        with database_manager.tenant(int(company_id)) as conn:
            found = conn.execute(
                "SELECT 1 FROM customers WHERE id = ? AND company_id = ? LIMIT 1",
                (int(customer_id), int(company_id)),
            ).fetchone()

        if not found:
            raise TelephonyError("That contact does not belong to this company.")

    @staticmethod
    def _employee_phone(company_id: int, employee_user_id: int) -> str:
        """The number a transfer rings, from the control plane.

        Active membership, not merely a user id: transferring a customer to
        somebody who has left the company would ring a number the company no
        longer controls.
        """
        with database_manager.control() as conn:
            row = conn.execute(
                """
                SELECT users.phone
                FROM company_users
                JOIN users ON users.id = company_users.user_id
                WHERE company_users.company_id = ?
                  AND company_users.user_id = ?
                  AND company_users.status = 'active'
                  AND users.status = 'active'
                LIMIT 1
                """,
                (int(company_id), int(employee_user_id)),
            ).fetchone()

        if not row:
            raise TelephonyError(
                "A call can only be transferred to an active member of this company."
            )

        if not row["phone"]:
            raise TelephonyError(
                "That colleague has no phone number on their profile to ring."
            )

        return str(row["phone"])

    @staticmethod
    def _locate_call(provider_call_id: str) -> tuple[int, dict[str, Any]] | None:
        """Which company's database holds the call the provider is talking about.

        One indexed lookup per active company. See the module docstring: with a
        database per company there is nothing else to ask, and a callback is a
        rare event rather than a hot path.
        """
        for company_id in database_manager.list_company_ids():
            try:
                with database_manager.tenant(company_id) as conn:
                    row = conn.execute(
                        """
                        SELECT * FROM telephony_calls
                        WHERE provider_call_id = ? LIMIT 1
                        """,
                        (provider_call_id,),
                    ).fetchone()
            except Exception:  # noqa: BLE001
                # One unreadable company must not stop the callback from
                # reaching the company it actually belongs to.
                logger.exception(
                    "Could not search company %s for call %s",
                    company_id,
                    provider_call_id,
                )
                continue

            if row:
                return company_id, dict(row)

        return None

    @staticmethod
    def _write_call_log(company_id: int, call: dict[str, Any]) -> None:
        """Mirror a finished call into the company's history.

        Never raises. A history entry that could not be written is worth a log
        line; it is not worth failing the provider's callback, which the
        provider would then retry against a call that is already over.
        """
        try:
            from backend.services.call_log_service import call_log_service

            direction = call.get("direction") or "outbound"
            other_number = (
                call.get("to_number")
                if direction == "outbound"
                else call.get("from_number")
            )

            notes = f"Dialer call via {call.get('provider') or 'telephony'}"

            if call.get("recording_url"):
                notes += f" — recording: {call['recording_url']}"

            call_log_service.create_call_log(
                company_id=company_id,
                direction=direction,
                phone_number=other_number,
                customer_id=call.get("customer_id"),
                duration_seconds=int(call.get("duration_seconds") or 0),
                status=_LOG_STATUS.get(str(call.get("status")), "completed"),
                notes=notes,
                actor_user_id=call.get("created_by"),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Could not write the call history entry for company %s", company_id
            )

    @staticmethod
    def _notify_inbound(
        company_id: int, provider_call_id: str, from_number: str | None
    ) -> None:
        try:
            from backend.services.notification_service import notification_service

            notification_service.create(
                company_id=company_id,
                notification_type="inbound_call",
                title="An inbound call was answered",
                body=(
                    f"Call from {from_number or 'an unknown number'} — "
                    "a message is being recorded."
                ),
                severity="info",
                data={"provider_call_id": provider_call_id},
                dedupe_key=f"inbound_call:{provider_call_id}",
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "Could not raise the inbound call notification for company %s",
                company_id,
            )


telephony_service = TelephonyService()
