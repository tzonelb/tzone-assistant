from core.engine import engine
from core.request import Request


class MessageGateway:
    def handle_text(self, channel, user_id, message, language=None, company_id=1):
        request = Request(
            channel=channel,
            user_id=str(user_id),
            language=language,
            message=message,
            company_id=company_id,
        )

        return engine.handle(request)


message_gateway = MessageGateway()