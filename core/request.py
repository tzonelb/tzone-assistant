from typing import Any


class Request:
    def __init__(
        self,
        channel,
        user_id,
        language=None,
        message="",
        company_id=1,
        workspace_id=1,
        branch_id=1,
        channel_account_id=None,
        external_conversation_id=None,
        external_message_id=None,
        message_type="text",
        attachments=None,
        location=None,
        reply_to=None,
        metadata=None,
        timestamp=None,
    ):
        self.workspace_id = workspace_id
        self.company_id = company_id
        self.branch_id = branch_id

        self.channel = channel
        self.channel_account_id = channel_account_id

        self.user_id = user_id
        self.language = language
        self.message = message

        self.external_conversation_id = (
            external_conversation_id
        )

        self.external_message_id = (
            external_message_id
        )

        self.message_type = message_type
        self.attachments = attachments or []
        self.location = location
        self.reply_to = reply_to
        self.metadata = metadata or {}
        self.timestamp = timestamp

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "company_id": self.company_id,
            "branch_id": self.branch_id,
            "channel": self.channel,
            "channel_account_id": self.channel_account_id,
            "user_id": self.user_id,
            "language": self.language,
            "message": self.message,
            "external_conversation_id": (
                self.external_conversation_id
            ),
            "external_message_id": (
                self.external_message_id
            ),
            "message_type": self.message_type,
            "attachments": self.attachments,
            "location": self.location,
            "reply_to": self.reply_to,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }