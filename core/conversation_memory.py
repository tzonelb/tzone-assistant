from datetime import datetime, timezone


class ConversationMemory:
    def append(self, user_session: dict, role: str, text: str):
        if not text:
            return

        if "conversation_history" not in user_session:
            user_session["conversation_history"] = []

        user_session["conversation_history"].append({
            "role": role,
            "text": text,
            # Aware UTC, like every other timestamp on the platform. This one
            # goes into the model's context, so a naive server-local clock
            # would both disagree with the stored message records by the
            # host's offset and give the model no offset to read it with.
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

        user_session["conversation_history"] = user_session["conversation_history"][-10:]

    def build_context(self, user_session: dict) -> dict:
        return {
            "conversation_history": user_session.get("conversation_history", []),
            "last_ai_department": user_session.get("last_ai_department"),
            "last_ai_intent": user_session.get("last_ai_intent"),
            "last_ai_reply": user_session.get("last_ai_reply"),
            "last_user_message": user_session.get("last_user_message"),
            "conversation_topic": user_session.get("conversation_topic"),
            "current_department": user_session.get("current_department"),
        }


conversation_memory = ConversationMemory()