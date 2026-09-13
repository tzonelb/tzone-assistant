""""Sending" a reply to a website chat visitor.

Every other channel here makes an outbound call to a provider -- Meta,
WhatsApp, Telegram's Bot API, Slack, Discord. Website chat has no provider to
call: the visitor's browser is the only client, and it is not this platform
that pushes to it. `backend/api/routes/webchat_widget.py`'s own polling
endpoint is what a reply actually reaches the visitor through, by reading the
same `messages` row every other reply writes.

So there is nothing to send here. This exists only so the channel has an
entry in the shared dispatcher (`channels/sender.py`) like every other
channel -- the caller (an employee's manual reply, a scheduled broadcast, the
assistant's own answer) writes the reply through `message_service.save_message`
immediately after this returns `ok`, exactly as it would for any channel, and
that write is what the widget's next poll picks up.
"""

from __future__ import annotations

from typing import Any


def send_webchat_text(
    *,
    recipient_id: str,
    text: str,
    company_id: int,
    buttons: list[str] | None = None,
) -> dict[str, Any]:
    return {"ok": True, "skipped": False}
