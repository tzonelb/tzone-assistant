from core.engine import engine
from core.request import Request


class MessageGateway:
    def handle_text(self, channel, user_id, message, language=None, company_id=None):
        request_kwargs = dict(
            channel=channel,
            user_id=str(user_id),
            language=language,
            message=message,
        )

        # Only override Request's default company_id when a caller actually
        # resolved one (e.g. the Meta pipeline). Other channels that don't
        # pass company_id keep Request's existing default behavior.
        if company_id is not None:
            request_kwargs["company_id"] = company_id

        request = Request(**request_kwargs)

        return engine.handle(request)


message_gateway = MessageGateway()