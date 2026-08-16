"""In-memory conversation state for the assistant's flow engine.

Two properties this needs that the previous version lacked:

* Sessions are keyed by company, channel and user together. Keying on the user
  id alone let one person's state on two channels collide, and offered no
  separation between companies at all.
* Sessions expire and the store is bounded. Previously every customer the
  platform had ever seen stayed in memory for the life of the process.

This is flow state, not customer data — it is safe to lose. Anything that must
survive a restart belongs in the company's database.
"""

from __future__ import annotations

import threading
import time

MAX_SESSIONS = 10_000
SESSION_TTL_SECONDS = 6 * 60 * 60


class SessionManager:
    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}
        self._touched: dict[str, float] = {}
        self._lock = threading.RLock()

    @staticmethod
    def key(user_id, channel: str = "", company_id: int | None = None) -> str:
        return f"{company_id or 0}:{str(channel).lower()}:{user_id}"

    def default_session(self) -> dict:
        return {
            "language": None,
            "state": "main_menu",
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

    def _evict(self) -> None:
        """Drop expired sessions, then the oldest if still over capacity."""
        now = time.monotonic()

        expired = [
            key
            for key, touched in self._touched.items()
            if now - touched > SESSION_TTL_SECONDS
        ]

        for key in expired:
            self.sessions.pop(key, None)
            self._touched.pop(key, None)

        if len(self.sessions) <= MAX_SESSIONS:
            return

        overflow = len(self.sessions) - MAX_SESSIONS
        oldest = sorted(self._touched.items(), key=lambda item: item[1])[:overflow]

        for key, _ in oldest:
            self.sessions.pop(key, None)
            self._touched.pop(key, None)

    def create(self, user_id) -> dict:
        key = str(user_id)

        with self._lock:
            if key not in self.sessions:
                self._evict()
                self.sessions[key] = self.default_session()

            self._touched[key] = time.monotonic()
            return self.sessions[key]

    def get(self, user_id) -> dict | None:
        with self._lock:
            session = self.sessions.get(str(user_id))

            if session is not None:
                self._touched[str(user_id)] = time.monotonic()

            return session

    def update(self, user_id, key, value) -> None:
        with self._lock:
            self.create(user_id)[key] = value

    def reset(self, user_id) -> dict:
        with self._lock:
            self.sessions[str(user_id)] = self.default_session()
            self._touched[str(user_id)] = time.monotonic()
            return self.sessions[str(user_id)]

    def set_language(self, user_id, language):
        with self._lock:
            session = self.create(user_id)

            if language in ("ar", "en"):
                session["language"] = language

            return session.get("language")

    def push_state(self, user_id, new_state):
        with self._lock:
            session = self.create(user_id)

            if not new_state:
                return session["state"]

            if session["state"] != new_state:
                session["history"].append(session["state"])
                # Bounded so a long conversation cannot grow without limit.
                session["history"] = session["history"][-50:]

            session["state"] = new_state
            return new_state

    def go_back(self, user_id):
        with self._lock:
            session = self.create(user_id)
            history = session["history"]

            while history:
                previous_state = history.pop()

                if previous_state != session["state"]:
                    session["state"] = previous_state
                    return previous_state

            return session["state"]

    def go_home(self, user_id, channel="messenger"):
        with self._lock:
            session = self.create(user_id)
            session["history"] = []
            session["state"] = "main_menu"
            session["current_department"] = None
            return session["state"]

    def stats(self) -> dict:
        with self._lock:
            return {"sessions": len(self.sessions), "capacity": MAX_SESSIONS}


session = SessionManager()
