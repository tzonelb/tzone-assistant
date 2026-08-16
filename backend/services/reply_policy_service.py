"""How one company answers — its own reply policy, resolved per channel.

Departments and the welcome are already the company's own. The *mechanism* was
not: ``core/response_policy.py`` read ``config/response_policy.json``, a single
file shared by the whole platform, and that one file decided for every business
whether a welcome is sent, how often, whether the assistant may answer freely,
how confident a knowledge match has to be and how many knowledge items reach the
model. One company could not loosen ``allow_ai_free_reply`` or tighten
``minimum_match_confidence`` without doing it to everybody.

What a company chooses now lives in that company's own encrypted database, in
the ``reply_policy`` section of ``company_settings`` — so it inherits the three
things that section already has: Super Admin locks, a change audit, and the
encryption at rest.

Resolution, most specific wins:

1. ``config/response_policy.json`` — the platform's shipped defaults, merged
   default-then-channel by ``core/response_policy``. Behaviour flags only:
   the file is shared, so nothing customer-facing may ever live in it again.
2. The company's own default, stored here.
3. The company's override for one specific channel, stored here.

A company's own default deliberately outranks the shipped *per-channel* value.
The shipped channel entries are the platform's starting suggestion, not a
decision this business made; once an owner says "my assistant may reply freely",
that has to hold on the channel the platform happened to ship as ``flow_only``
too, or the switch on the screen would silently do nothing on that channel.

Stored shape — sparse on purpose::

    {
        "default":  {"allow_ai_free_reply": true},
        "channels": {"telegram": {"show_buttons": false}}
    }

Only what the company actually chose is written. A key that is absent inherits,
which is what makes "clear this override" a real operation rather than a value
copied down from the level above and frozen there.

Writes are validated before they are stored. An unknown key, a ``welcome_mode``
that is not a real mode, or a confidence outside 0..1 is refused rather than
kept: a stored typo reads back exactly like a decision that was applied and
changes nothing, so the operator believes they tightened the assistant when they
have not. This is the same reasoning as ``_validate_modules`` /
``_validate_branding`` in ``backend/services/platform_service.py``.

Reads are tolerant instead, because they happen on the customer reply path.
Anything unrecognised in storage is dropped with a warning and the reply goes
out on the level above rather than not at all.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.services.company_settings_service import company_settings_service


logger = logging.getLogger(__name__)


SETTINGS_SECTION = "reply_policy"

# Kept in step with ``bot_profile_service.PREVIEW_CHANNELS`` and
# ``config.settings.SUPPORTED_CHANNELS``. A channel outside this list has no
# policy to resolve, so naming one is refused rather than stored under a key
# nothing will ever read.
POLICY_CHANNELS = (
    "messenger",
    "instagram",
    "whatsapp",
    "telegram",
    "website_chat",
)

WELCOME_MODES = ("always", "once_per_conversation", "never")

# The three the platform actually ships and the engine understands:
# ``config/response_policy.json`` uses ``grounded_ai`` and ``flow_only``, and
# ``ResponsePolicy.DEFAULT_POLICY`` falls back to ``knowledge_then_ai``.
REPLY_MODES = ("grounded_ai", "knowledge_then_ai", "flow_only")

# ``core/engine.py`` clamps the value it reads to 1..5 before asking the
# knowledge matcher for that many items. Accepting 40 here would store a number
# that reads back as a decision and changes nothing past 5.
MIN_KNOWLEDGE_RESULTS = 1
MAX_KNOWLEDGE_RESULTS = 5

# Confidence is stored as a fraction; more precision than this is noise from
# float arithmetic rather than a choice anybody made.
CONFIDENCE_DECIMALS = 4


class ReplyPolicyError(ValueError):
    """A reply-policy change that was refused, with a reason worth showing.

    A ``ValueError`` so that the generic company-settings route, which does not
    import this module, still turns it into a 409 with the message intact.
    """


# The editable surface, in the order the screen renders it. ``help`` is shown
# under the control: every one of these decides something a customer
# experiences, and an owner should not have to guess which.
FIELDS: tuple[dict[str, Any], ...] = (
    {
        "key": "welcome_enabled",
        "type": "boolean",
        "label": "Send a welcome message",
        "help": (
            "Whether the greeting you wrote above is prefixed to a reply at "
            "all. The words themselves stay yours."
        ),
    },
    {
        "key": "welcome_mode",
        "type": "choice",
        "label": "How often the welcome is sent",
        "choices": list(WELCOME_MODES),
        "help": (
            "Once per conversation greets a customer when they come back after "
            "a break; always greets on every single message."
        ),
    },
    {
        "key": "reply_mode",
        "type": "choice",
        "label": "How a reply is produced",
        "choices": list(REPLY_MODES),
        "help": (
            "Grounded AI answers from your knowledge; flow only sticks to the "
            "buttons and scripted steps."
        ),
    },
    {
        "key": "grounded_ai_enabled",
        "type": "boolean",
        "label": "Answer from your knowledge",
        "help": "Off means the assistant never composes an answer from knowledge items.",
    },
    {
        "key": "allow_ai_free_reply",
        "type": "boolean",
        "label": "Allow a free reply with no knowledge match",
        "help": (
            "On lets the assistant answer when nothing in your knowledge "
            "matched. Off keeps it to what you taught it."
        ),
    },
    {
        "key": "minimum_match_confidence",
        "type": "fraction",
        "label": "Minimum match confidence",
        "minimum": 0.0,
        "maximum": 1.0,
        "step": 0.01,
        "help": "How sure the match has to be, from 0 to 1, before it is used.",
    },
    {
        "key": "maximum_knowledge_results",
        "type": "integer",
        "label": "Knowledge items per answer",
        "minimum": MIN_KNOWLEDGE_RESULTS,
        "maximum": MAX_KNOWLEDGE_RESULTS,
        "step": 1,
        "help": "How many of your knowledge items the assistant may use in one reply.",
    },
    {
        "key": "fallback_to_human",
        "type": "boolean",
        "label": "Hand over to a human when unsure",
        "help": "Off means the assistant answers anyway instead of escalating.",
    },
    {
        "key": "show_buttons",
        "type": "boolean",
        "label": "Show quick-reply buttons",
        "help": "Off delivers the text only, with no buttons under it.",
    },
)

POLICY_KEYS: tuple[str, ...] = tuple(field["key"] for field in FIELDS)

_FIELD_BY_KEY: dict[str, dict[str, Any]] = {field["key"]: field for field in FIELDS}


class ReplyPolicyService:
    # ------------------------------------------------------------------
    # Validation — refuse, never coerce
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_boolean(key: str, value: Any) -> bool:
        # Deliberately not ``bool(value)``: that turns the string "false" into
        # ``True``, which is a switch that reads as off on the screen and is on
        # in the engine.
        if not isinstance(value, bool):
            raise ReplyPolicyError(
                f"{key} must be true or false, not {value!r}."
            )

        return value

    @staticmethod
    def _validate_choice(key: str, value: Any, choices: tuple[str, ...]) -> str:
        text = str(value or "").strip().lower()

        if text not in choices:
            raise ReplyPolicyError(
                f"{key} must be one of: {', '.join(choices)} — not {value!r}."
            )

        return text

    @staticmethod
    def _validate_confidence(key: str, value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ReplyPolicyError(
                f"{key} must be a number between 0 and 1, not {value!r}."
            )

        number = float(value)

        if not 0.0 <= number <= 1.0:
            raise ReplyPolicyError(
                f"{key} must be between 0 and 1, not {number!r}."
            )

        return round(number, CONFIDENCE_DECIMALS)

    @staticmethod
    def _validate_results(key: str, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ReplyPolicyError(
                f"{key} must be a whole number between {MIN_KNOWLEDGE_RESULTS} "
                f"and {MAX_KNOWLEDGE_RESULTS}, not {value!r}."
            )

        if not MIN_KNOWLEDGE_RESULTS <= value <= MAX_KNOWLEDGE_RESULTS:
            raise ReplyPolicyError(
                f"{key} must be between {MIN_KNOWLEDGE_RESULTS} and "
                f"{MAX_KNOWLEDGE_RESULTS}, not {value!r}."
            )

        return int(value)

    def validate_value(self, key: str, value: Any) -> Any:
        field = _FIELD_BY_KEY.get(key)

        if field is None:
            raise ReplyPolicyError(
                f"Unknown reply policy setting: {key}. "
                f"Valid settings are: {', '.join(POLICY_KEYS)}."
            )

        kind = field["type"]

        if kind == "boolean":
            return self._validate_boolean(key, value)

        if kind == "choice":
            return self._validate_choice(key, value, tuple(field["choices"]))

        if kind == "fraction":
            return self._validate_confidence(key, value)

        return self._validate_results(key, value)

    def validate_values(self, values: Any) -> dict[str, Any]:
        """One scope's overrides: every key known, every value in range."""
        if not isinstance(values, dict):
            raise ReplyPolicyError(
                "A reply policy is a mapping of setting to value."
            )

        unknown = sorted(set(values) - set(POLICY_KEYS))

        if unknown:
            raise ReplyPolicyError(
                f"Unknown reply policy setting(s): {', '.join(unknown)}. "
                f"Valid settings are: {', '.join(POLICY_KEYS)}."
            )

        return {key: self.validate_value(key, value) for key, value in values.items()}

    def validate_channel(self, channel: Any) -> str:
        text = str(channel or "").strip().lower()

        if text not in POLICY_CHANNELS:
            raise ReplyPolicyError(
                f"Unknown channel: {channel!r}. "
                f"Valid channels are: {', '.join(POLICY_CHANNELS)}."
            )

        return text

    def _validate_channels(self, channels: Any) -> dict[str, Any]:
        """The channels half of the section, on its own.

        Written separately from the company default so that saving a channel
        never restates — and so never silently rewrites — the default beside it.
        """
        if not isinstance(channels, dict):
            raise ReplyPolicyError(
                "'channels' is a mapping of channel name to its overrides."
            )

        clean: dict[str, Any] = {}

        for name, overrides in channels.items():
            channel = self.validate_channel(name)
            values = self.validate_values(overrides or {})

            # An empty override map means "inherits", which is stored as the
            # absence of the channel rather than as an empty row.
            if values:
                clean[channel] = values

        return clean

    def validate_section(self, values: Any) -> dict[str, Any]:
        """The whole stored section, as it will be written.

        Called both by this service's own writers and by
        ``company_settings_service.update_section``, so a write that goes
        straight at ``/api/company-settings/reply_policy`` is held to exactly
        the same rules as the AI TEACHING screen.
        """
        if not isinstance(values, dict):
            raise ReplyPolicyError(
                "The reply policy is a mapping with 'default' and 'channels'."
            )

        unknown = sorted(set(values) - {"default", "channels"})

        if unknown:
            raise ReplyPolicyError(
                f"Unknown reply policy key(s): {', '.join(unknown)}. "
                "The reply policy holds 'default' and 'channels' only."
            )

        return {
            "default": self.validate_values(values.get("default") or {}),
            "channels": self._validate_channels(values.get("channels") or {}),
        }

    def merge_section(
        self,
        current: Any,
        incoming: Any,
        *,
        company_id: int = 0,
    ) -> dict[str, Any]:
        """What to store when a partial write arrives.

        Strict about what is arriving — that is where a typo comes from — and
        tolerant about what was already stored, so one unusable value written
        by hand into the database cannot make the section unwritable from the
        very screen that would correct it.
        """
        if not isinstance(incoming, dict):
            raise ReplyPolicyError(
                "The reply policy is a mapping with 'default' and 'channels'."
            )

        unknown = sorted(set(incoming) - {"default", "channels"})

        if unknown:
            raise ReplyPolicyError(
                f"Unknown reply policy key(s): {', '.join(unknown)}. "
                "The reply policy holds 'default' and 'channels' only."
            )

        shape = self._safe_shape(current, company_id=company_id)
        validated = self.validate_section(incoming)

        for key in ("default", "channels"):
            if key in incoming:
                shape[key] = validated[key]

        return shape

    # ------------------------------------------------------------------
    # Reading — tolerant, because this runs on the reply path
    # ------------------------------------------------------------------

    def _safe_values(self, values: Any, *, company_id: int, scope: str) -> dict[str, Any]:
        if not isinstance(values, dict):
            return {}

        clean: dict[str, Any] = {}

        for key, value in values.items():
            try:
                clean[key] = self.validate_value(key, value)
            except ReplyPolicyError:
                logger.warning(
                    "Ignoring unusable reply policy setting %s in %s for "
                    "company %s; the level above applies instead.",
                    key,
                    scope,
                    company_id,
                )

        return clean

    def _safe_shape(self, values: Any, *, company_id: int) -> dict[str, Any]:
        if not isinstance(values, dict):
            return {"default": {}, "channels": {}}

        default = self._safe_values(
            values.get("default"), company_id=company_id, scope="the company default"
        )

        raw_channels = values.get("channels")
        channels: dict[str, dict[str, Any]] = {}

        if isinstance(raw_channels, dict):
            for name, overrides in raw_channels.items():
                channel = str(name or "").strip().lower()

                if channel not in POLICY_CHANNELS:
                    logger.warning(
                        "Ignoring reply policy for unknown channel %r of "
                        "company %s.",
                        name,
                        company_id,
                    )
                    continue

                clean = self._safe_values(
                    overrides, company_id=company_id, scope=channel
                )

                if clean:
                    channels[channel] = clean

        return {"default": default, "channels": channels}

    def stored(self, company_id: int) -> dict[str, Any]:
        """What this company has actually chosen, and nothing else."""
        section = company_settings_service.get_section(company_id, SETTINGS_SECTION)

        return self._safe_shape(section.get("values"), company_id=int(company_id))

    def overrides_for(self, company_id: int | None, channel: str) -> dict[str, Any]:
        """The company's default plus its override for this channel, merged.

        Never raises. A company that has chosen nothing, a message with no
        company, or a database that will not open all resolve to ``{}`` — which
        leaves the caller on the platform's shipped defaults rather than
        leaving the customer without a reply.
        """
        if not company_id:
            return {}

        try:
            stored = self.stored(int(company_id))
        except Exception:
            logger.exception(
                "Could not read the reply policy of company %s; "
                "the platform defaults apply.",
                company_id,
            )
            return {}

        merged = dict(stored["default"])
        merged.update(stored["channels"].get(str(channel or "").strip().lower(), {}))

        return merged

    def apply(
        self,
        base: dict[str, Any],
        *,
        company_id: int | None,
        channel: str,
    ) -> dict[str, Any]:
        """``base`` is the platform's shipped policy for this channel."""
        merged = dict(base)
        merged.update(self.overrides_for(company_id, channel))

        return merged

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def _clear_keys(self, keys: Any) -> list[str]:
        if keys is None:
            return []

        if not isinstance(keys, (list, tuple, set)):
            raise ReplyPolicyError("The settings to clear are given as a list.")

        cleared = []

        for key in keys:
            name = str(key or "").strip()

            if name not in POLICY_KEYS:
                raise ReplyPolicyError(
                    f"Unknown reply policy setting: {name or key!r}. "
                    f"Valid settings are: {', '.join(POLICY_KEYS)}."
                )

            cleared.append(name)

        return cleared

    def _save(
        self,
        *,
        company_id: int,
        values: dict[str, Any],
        actor_user_id: int | None,
    ) -> None:
        """Write one part of the section — 'default' or 'channels', not both.

        Narrow on purpose. A Super Admin lock is recorded per key of a section,
        so writing both parts on every save would make a lock on the company
        default refuse an edit to a single channel, and the message would blame
        a setting the operator never touched.
        """
        company_settings_service.update_section(
            company_id=int(company_id),
            section=SETTINGS_SECTION,
            values=values,
            actor_user_id=actor_user_id,
        )

    def update_company_default(
        self,
        *,
        company_id: int,
        values: dict[str, Any] | None = None,
        clear: Any = None,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        """Set or clear keys on the company's own default."""
        company_id = int(company_id)
        cleared = self._clear_keys(clear)
        changes = self.validate_values(values or {})

        current = self.stored(company_id)
        default = dict(current["default"])

        for key in cleared:
            default.pop(key, None)

        default.update(changes)

        self._save(
            company_id=company_id,
            values={"default": self.validate_values(default)},
            actor_user_id=actor_user_id,
        )

        return self.stored(company_id)

    def update_channel(
        self,
        *,
        company_id: int,
        channel: str,
        values: dict[str, Any] | None = None,
        clear: Any = None,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        """Set or clear keys on one channel's override.

        Clearing every key on a channel removes the override entirely, so the
        channel goes back to inheriting rather than keeping a frozen copy of
        whatever the company default happened to be when it was set.
        """
        company_id = int(company_id)
        name = self.validate_channel(channel)
        cleared = self._clear_keys(clear)
        changes = self.validate_values(values or {})

        current = self.stored(company_id)
        channels = {key: dict(value) for key, value in current["channels"].items()}
        overrides = dict(channels.get(name, {}))

        for key in cleared:
            overrides.pop(key, None)

        overrides.update(changes)

        if overrides:
            channels[name] = overrides
        else:
            channels.pop(name, None)

        self._save(
            company_id=company_id,
            values={"channels": self._validate_channels(channels)},
            actor_user_id=actor_user_id,
        )

        return self.stored(company_id)

    def clear_channel(
        self,
        *,
        company_id: int,
        channel: str,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        """Drop a channel's whole override; it inherits the company default again."""
        company_id = int(company_id)
        name = self.validate_channel(channel)

        current = self.stored(company_id)
        channels = {
            key: dict(value)
            for key, value in current["channels"].items()
            if key != name
        }

        self._save(
            company_id=company_id,
            values={"channels": self._validate_channels(channels)},
            actor_user_id=actor_user_id,
        )

        return self.stored(company_id)

    # ------------------------------------------------------------------
    # The editor's view
    # ------------------------------------------------------------------

    def describe(self, company_id: int, shipped: dict[str, Any]) -> dict[str, Any]:
        """Everything the screen needs to show inheritance honestly.

        ``shipped`` is ``{"default": {...}, "channels": {channel: {...}}}`` as
        resolved from ``config/response_policy.json`` by
        ``core/response_policy``; it is passed in rather than imported so this
        service stays underneath ``core`` rather than in a cycle with it.

        For every scope the payload carries three things, because showing only
        the effective value is what makes a control look set when it is
        inherited: what applies (``values``), what this scope actually chose
        (``overrides``), and what it would fall back to (``inherited``).
        """
        company_id = int(company_id)
        section = company_settings_service.get_section(company_id, SETTINGS_SECTION)
        stored = self._safe_shape(section.get("values"), company_id=company_id)

        shipped_default = dict(shipped.get("default") or {})
        shipped_channels = shipped.get("channels") or {}

        company_default = {**shipped_default, **stored["default"]}

        channels = []

        for channel in POLICY_CHANNELS:
            shipped_channel = dict(shipped_channels.get(channel) or shipped_default)
            inherited = {**shipped_channel, **stored["default"]}
            overrides = dict(stored["channels"].get(channel, {}))

            channels.append(
                {
                    "channel": channel,
                    "shipped": shipped_channel,
                    "inherited": inherited,
                    "overrides": overrides,
                    "values": {**inherited, **overrides},
                }
            )

        return {
            "section": SETTINGS_SECTION,
            "fields": [dict(field) for field in FIELDS],
            "locked_keys": list(section.get("locked_keys") or []),
            "shipped_default": shipped_default,
            "default": {
                "scope": "default",
                "shipped": shipped_default,
                "inherited": shipped_default,
                "overrides": dict(stored["default"]),
                "values": company_default,
            },
            "channels": channels,
        }


reply_policy_service = ReplyPolicyService()
