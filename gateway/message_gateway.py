"""Entry point from a channel into the assistant engine.

Everything the engine needs to know *whose* message this is has to arrive
here, because nothing below this point can look it up. That is the company, and
— just as importantly — the channel account the message arrived on: a company
may connect three Instagram accounts and point each at a different department,
so the account is what decides where the conversation belongs.

``channel_account_id`` used to stop at the webhook. ``Request`` had the field
and every caller left it ``None``, so the identity chain the platform is built
on — company → channel account → department → employee — was broken at its
second link before the engine ever ran.
"""

from core.engine import engine
from core.request import Request


class MessageGateway:
    """Entry point from a channel into the assistant engine."""

    def handle_text(
        self,
        channel,
        user_id,
        company_id,
        message,
        language=None,
        channel_account_id=None,
    ):
        request = Request(
            channel=channel,
            user_id=str(user_id),
            company_id=int(company_id),
            channel_account_id=(
                int(channel_account_id)
                if channel_account_id is not None
                else None
            ),
            language=language,
            message=message,
        )

        return engine.handle(request)


message_gateway = MessageGateway()
