from core.engine import engine
from core.request import Request


class MessageGateway:
    """Entry point from a channel into the assistant engine."""

    def handle_text(self, channel, user_id, company_id, message, language=None):
        request = Request(
            channel=channel,
            user_id=str(user_id),
            company_id=int(company_id),
            language=language,
            message=message,
        )

        return engine.handle(request)


message_gateway = MessageGateway()
