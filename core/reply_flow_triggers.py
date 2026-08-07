"""Registry of Reply Flow trigger types.

Today, a flow is picked implicitly the moment ANY new customer message
arrives on a matching channel/department — that behavior is now named
`new_conversation` and is the default for every flow, existing or new,
so nothing already saved in the database changes behavior.

This module is the single source of truth for what trigger types exist
and what config each one takes — reply_flow_service.py (schema/CRUD/AI
generation prompt) and reply_flow_engine.py (matching + firing) both
import from here instead of hardcoding the list twice. The frontend
mirrors this shape (see nodeFieldsConfig.js's NODE_FIELDS and
ReplyFlowBuilderPage.jsx's FALLBACK_TRIGGER_TYPES) and can also fetch it
live from GET /api/reply-flows/trigger-types.

config_fields is shaped exactly like NODE_FIELDS entries: a list of
{key, label, type, placeholder?, hint?, options?} dicts.

Deliberately small: this is the first real batch (the clearest, most
valuable ones), not the full ~55 trigger ideas floated during design —
more get added here later, one entry at a time, without touching the
engine's matching logic.
"""
from __future__ import annotations

DEFAULT_TRIGGER_TYPE = "new_conversation"

TRIGGER_TYPES: dict[str, dict] = {
    "new_conversation": {
        "label": "New conversation",
        "category": "Conversation",
        "description": (
            "Starts the moment a customer's first message arrives on a matching "
            "channel/department — today's only trigger, kept as the default so "
            "every existing flow keeps working exactly as before."
        ),
        "config_fields": [],
    },
    "conversation_closed": {
        "label": "Conversation closed",
        "category": "Conversation",
        "description": (
            "Starts a fresh, separate mini-flow right after a conversation is "
            "marked done/closed — e.g. to ask the customer for a rating."
        ),
        "config_fields": [],
    },
    "appointment_created": {
        "label": "Appointment created",
        "category": "Appointments",
        "description": "Starts right after a new appointment is booked.",
        "config_fields": [],
    },
    "appointment_completed": {
        "label": "Appointment completed",
        "category": "Appointments",
        "description": "Starts when an appointment's status changes to completed.",
        "config_fields": [],
    },
    "appointment_reminder": {
        "label": "Appointment reminder",
        "category": "Appointments",
        "description": (
            "Starts ahead of a scheduled appointment, once per appointment "
            "(never fires twice for the same one)."
        ),
        "config_fields": [
            {
                "key": "minutes_before",
                "label": "Minutes before the appointment",
                "type": "number",
                "placeholder": "60",
            },
        ],
    },
    "call_logged": {
        "label": "Call logged",
        "category": "Calls",
        "description": "Starts right after a call is logged.",
        "config_fields": [],
    },
    "task_completed": {
        "label": "Task completed",
        "category": "Tasks",
        "description": (
            "Starts when a task linked to this customer is marked done — "
            "e.g. to tell them their request or complaint has been resolved."
        ),
        "config_fields": [],
    },
    "customer_no_reply": {
        "label": "Customer went silent",
        "category": "Conversation",
        "description": (
            "Starts when the customer has not replied for a set number of "
            "minutes after our last message — e.g. to nudge them about an "
            "unanswered quote. Fires once per silence period (a new customer "
            "message re-arms it)."
        ),
        "config_fields": [
            {
                "key": "minutes_of_silence",
                "label": "Minutes of customer silence",
                "type": "number",
                "placeholder": "60",
            },
        ],
    },
    "team_no_reply": {
        "label": "Team hasn't replied",
        "category": "Conversation",
        "description": (
            "Starts when a customer has been waiting on a human reply for a "
            "set number of minutes — e.g. to apologize for the delay and "
            "offer self-service options. Fires once per waiting period."
        ),
        "config_fields": [
            {
                "key": "minutes_waiting",
                "label": "Minutes the customer has been waiting",
                "type": "number",
                "placeholder": "30",
            },
        ],
    },
}
