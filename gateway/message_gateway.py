from core.engine import engine
from core.reply_flow_engine import reply_flow_engine
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

        flow_response = reply_flow_engine.maybe_handle(request)
        if flow_response is not None:
            return flow_response

        return engine.handle(request)


message_gateway = MessageGateway()