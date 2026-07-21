class SessionManager:
    def __init__(self):
        self.sessions = {}

    def default_session(self):
        return {
            "language": None,
            "state": "telegram_iptv_start",
            "history": [],

            "ticket_id": None,
            "iptv_username": None,
            "device": None,
            "os": None,
            "app": None,
            "problem": None,

            "current_department": None,
            "last_user_message": None,
            "last_ai_department": None,
            "last_ai_intent": None,
            "last_ai_confidence": None,
            "last_ai_needs_human": None,
            "last_ai_reply": None,
            "conversation_topic": None,
            "conversation_history": [],
            "welcome_sent": False,
        }

    def create(self, user_id):
        if user_id not in self.sessions:
            self.sessions[user_id] = self.default_session()

        return self.sessions[user_id]

    def get(self, user_id):
        return self.sessions.get(user_id)

    def update(self, user_id, key, value):
        self.create(user_id)
        self.sessions[user_id][key] = value

    def reset(self, user_id):
        self.sessions[user_id] = self.default_session()
        return self.sessions[user_id]

    def set_language(self, user_id, language):
        self.create(user_id)

        if language in ["ar", "en"]:
            self.sessions[user_id]["language"] = language

        return self.sessions[user_id].get("language")

    def push_state(self, user_id, new_state):
        self.create(user_id)

        if not new_state:
            return self.sessions[user_id]["state"]

        current_state = self.sessions[user_id]["state"]

        if current_state != new_state:
            self.sessions[user_id]["history"].append(current_state)

        self.sessions[user_id]["state"] = new_state
        return new_state

    def go_back(self, user_id):
        self.create(user_id)

        history = self.sessions[user_id]["history"]

        while history:
            previous_state = history.pop()

            if previous_state != self.sessions[user_id]["state"]:
                self.sessions[user_id]["state"] = previous_state
                return previous_state

        return self.sessions[user_id]["state"]

    def go_home(self, user_id, channel="telegram"):
        self.create(user_id)
        self.sessions[user_id]["history"] = []

        if channel == "telegram":
            self.sessions[user_id]["state"] = "iptv_menu"
            self.sessions[user_id]["current_department"] = "iptv"
        else:
            self.sessions[user_id]["state"] = "main_menu"
            self.sessions[user_id]["current_department"] = None

        return self.sessions[user_id]["state"]


session = SessionManager()