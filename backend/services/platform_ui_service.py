"""Theme Studio: drafting, publishing and resolving the interface's design tokens.

`platform_service.update_theme` publishes one company's tokens in a single
write, with no history and nothing between deciding and everybody seeing it.
Theme Studio needs the step in between, so this module adds the shape the design
specification describes and nothing else:

* a **draft** an administrator edits control by control, saved as they go and
  visible to nobody;
* a **publish**, which turns that draft into the next numbered version, archives
  the one it replaces and writes an audit row saying why;
* a **restore**, which opens an archived version again as a fresh draft.

WHAT A ROW HOLDS
----------------
A patch, not a snapshot. A row stores only the tokens its author actually
changed, so `resolve` can merge platform → plan → company key by key. Storing
merged snapshots instead meant a draft silently reset every token it did not
touch back to the bundled default rather than inheriting the layer beneath it.

WHERE IT LIVES
--------------
`database_manager.control()`, always — the table is declared in
`database/schema_control.py` and never created here. The platform layer belongs
to no company and could not live in one, and the read path resolves how a
workspace looks before that workspace's encrypted database is opened. Nothing
customer-owned is stored: a design token is a colour, a font name and a number
of pixels.

WHAT A THEME MAY NOT DO
-----------------------
Widen the operator's module gate. `modules` in a theme decides what appears in
the menu, and the resolution below can only ever turn an entry *off*: a module
the platform operator switched off for a company stays off however the theme is
written. Styling a workspace must never be a way to reach a feature.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import re
from typing import Any

from backend.services.platform_service import (
    DEFAULT_THEME_TOKENS,
    MAX_BRANDING_VALUE,
    PLATFORM_MODULES,
    platform_service,
)
from database.manager import database_manager, utc_now_iso


logger = logging.getLogger(__name__)


# `platform` reaches every workspace, `plan` the companies on one plan, and
# `company` is a single workspace's own override. They resolve in that order.
SCOPE_TYPES: tuple[str, ...] = ("platform", "plan", "company")

# The same keys the operator's module gate uses, deliberately: a theme names
# modules to hide them from the menu, and a key this platform does not have is a
# key that could never take effect.
MODULE_KEYS: tuple[str, ...] = PLATFORM_MODULES

# Only these values render. A token outside them is not a preference the
# interface can honour, it is a workspace that draws wrong — which is the
# failure this whole validation layer exists to prevent.
ALLOWED_FONTS: tuple[str, ...] = (
    "Inter",
    "Cormorant Garamond",
    "Lora",
    "IBM Plex Sans",
    "Manrope",
    "Cairo",
)
ALLOWED_MODES: tuple[str, ...] = ("light", "dark")
ALLOWED_RAILS: tuple[str, ...] = ("paper", "ink", "accent")
ALLOWED_BUTTONS: tuple[str, ...] = ("outline", "soft", "solid")
ALLOWED_SHADOWS: tuple[str, ...] = ("none", "sm", "md")
ALLOWED_DIRECTIONS: tuple[str, ...] = ("auto", "ltr", "rtl")

# Each range is the range of the control that edits it in Theme Studio. They are
# here as well as there because the screen is not the enforcement: a number
# outside these bounds is a rail nobody can reach past or type too small to
# read, and the API is what has to refuse it.
NUMERIC_RANGES: dict[tuple[str, str], tuple[float, float]] = {
    ("type", "baseSize"): (12, 18),
    ("type", "headingScale"): (0.85, 1.25),
    ("shape", "radius"): (0, 24),
    ("layout", "density"): (0.75, 1.2),
    ("layout", "railWidth"): (180, 300),
}

_COLOR_PATTERN = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class PlatformUiError(ValueError):
    """A theme the platform will not store."""


class PlatformUiNotFound(LookupError):
    """No theme with that id."""


def _validate_tokens_patch(candidate: Any) -> dict[str, Any]:
    """Check a *sparse* token patch — only the keys a draft actually overrides.

    Unknown groups and keys are refused rather than dropped. This is the
    opposite of `platform_service._validate_theme`, and deliberately so: that
    one serves a company's own published theme, where a newer screen posting a
    token an older API has not heard of should still save what it can. This one
    serves an authoring tool, where a key that will never take effect is a
    control the administrator watched do nothing.
    """
    if not isinstance(candidate, dict):
        raise PlatformUiError("tokens must be an object.")

    cleaned: dict[str, dict[str, Any]] = {}

    for group, values in candidate.items():
        if group not in DEFAULT_THEME_TOKENS:
            raise PlatformUiError(f'Unknown token section "{group}".')

        if not isinstance(values, dict):
            raise PlatformUiError(f'Token section "{group}" must be an object.')

        section: dict[str, Any] = {}

        for key, value in values.items():
            if key not in DEFAULT_THEME_TOKENS[group]:
                raise PlatformUiError(f'Unknown token "{group}.{key}".')

            section[key] = value

        if section:
            cleaned[group] = section

    color = cleaned.get("color", {})

    for key, allowed in (("mode", ALLOWED_MODES), ("rail", ALLOWED_RAILS)):
        if key in color and color[key] not in allowed:
            raise PlatformUiError(
                f"color.{key} must be one of {', '.join(allowed)}."
            )

    for key in ("accent", "accent2"):
        if key in color:
            text = str(color[key]).strip()

            if not _COLOR_PATTERN.match(text):
                raise PlatformUiError(
                    f"color.{key} must be a hex colour such as #1689e8."
                )

            color[key] = text

    type_tokens = cleaned.get("type", {})

    for key in ("headingFont", "bodyFont"):
        if key in type_tokens and type_tokens[key] not in ALLOWED_FONTS:
            raise PlatformUiError(
                f"type.{key} must be one of {', '.join(ALLOWED_FONTS)}."
            )

    shape = cleaned.get("shape", {})

    for key, allowed in (("buttons", ALLOWED_BUTTONS), ("shadow", ALLOWED_SHADOWS)):
        if key in shape and shape[key] not in allowed:
            raise PlatformUiError(
                f"shape.{key} must be one of {', '.join(allowed)}."
            )

    if "cardFill" in shape and not isinstance(shape["cardFill"], bool):
        raise PlatformUiError("shape.cardFill must be true or false.")

    layout = cleaned.get("layout", {})

    if "direction" in layout and layout["direction"] not in ALLOWED_DIRECTIONS:
        raise PlatformUiError(
            f"layout.direction must be one of {', '.join(ALLOWED_DIRECTIONS)}."
        )

    for (group, key), (low, high) in NUMERIC_RANGES.items():
        if key not in cleaned.get(group, {}):
            continue

        value = cleaned[group][key]

        # bool first: `isinstance(True, int)` is True in Python, so a boolean
        # would otherwise pass as a number.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PlatformUiError(f"{group}.{key} must be a number.")

        # Infinity and NaN are floats and serialise to JSON that no other
        # parser will read back. They are refused before they reach the shared
        # control database rather than after.
        if not math.isfinite(value) or not low <= value <= high:
            raise PlatformUiError(
                f"{group}.{key} must be a number between {low} and {high}."
            )

    return cleaned


def _validate_modules_patch(candidate: Any) -> dict[str, Any]:
    """Check a sparse module patch — same patch-not-snapshot model as tokens."""
    if not isinstance(candidate, dict):
        raise PlatformUiError("modules must be an object.")

    cleaned: dict[str, dict[str, Any]] = {}

    for key, entry in candidate.items():
        if key not in MODULE_KEYS:
            raise PlatformUiError(f'Unknown module "{key}".')

        if not isinstance(entry, dict):
            raise PlatformUiError(f'Module "{key}" must be an object.')

        values: dict[str, Any] = {}

        if "visible" in entry:
            if not isinstance(entry["visible"], bool):
                raise PlatformUiError(f'Module "{key}".visible must be true or false.')

            values["visible"] = entry["visible"]

        if "label" in entry:
            label = entry["label"]

            if label is not None and not isinstance(label, str):
                raise PlatformUiError(f'Module "{key}".label must be text or null.')

            if isinstance(label, str):
                label = label.strip()

                # An unbounded string here would sit in the *shared* control
                # database and be re-read on every workspace configuration
                # request. Same bound, and the same reason, as the branding
                # fields next door.
                if len(label) > MAX_BRANDING_VALUE:
                    raise PlatformUiError(
                        f'Module "{key}".label cannot be longer than '
                        f"{MAX_BRANDING_VALUE} characters."
                    )

            values["label"] = label

        if "order" in entry:
            if not isinstance(entry["order"], int) or isinstance(entry["order"], bool):
                raise PlatformUiError(f'Module "{key}".order must be a whole number.')

            values["order"] = entry["order"]

        if values:
            cleaned[key] = values

    return cleaned


def _merge_patch(accumulator: dict[str, Any], patch: dict[str, Any] | None) -> dict[str, Any]:
    """Lay one sparse patch over another, one section at a time.

    Later wins per key; a key the patch does not mention keeps the value the
    layer beneath it gave it. This is what makes the layering work: a company
    override of `color.accent` must not also reset that company's font.
    """
    if not patch:
        return accumulator

    merged = copy.deepcopy(accumulator)

    for section, values in patch.items():
        if not isinstance(values, dict):
            continue

        merged.setdefault(section, {}).update(values)

    return merged


class PlatformUiService:
    # A published version is never edited, so the history only grows by
    # publishing. This bounds what one scope's history page can ask for.
    MAX_THEMES = 200

    # ------------------------------------------------------------------
    # Reading: the path every workspace takes on sign-in
    # ------------------------------------------------------------------

    def resolve(self, *, company_id: int | None) -> dict[str, Any]:
        """The tokens and menu this session should render with.

        Four layers, each one a patch over the last:

        1. ``DEFAULT_THEME_TOKENS`` — what the platform looks like with nothing
           published anywhere, so adding this layer is a visual no-op until
           somebody deliberately changes something;
        2. the published **platform** theme;
        3. the published theme for this company's **plan**;
        4. this company's own — first a published **company**-scope theme, then
           whatever ``platform_service.update_theme`` last wrote for it, which
           is the company's own live override and therefore the last word.

        ``company_id`` is None for a caller that belongs to no company. Such a
        session gets the platform layer and stops there, which is the only
        layer it is entitled to.
        """
        tokens = copy.deepcopy(DEFAULT_THEME_TOKENS)
        modules: dict[str, Any] = {}
        version = 0

        with database_manager.control() as conn:
            plan_code = None

            if company_id is not None:
                row = conn.execute(
                    """
                    SELECT p.code AS code
                    FROM subscriptions s
                    JOIN plans p ON p.id = s.plan_id
                    WHERE s.company_id = ?
                      AND s.status IN ('active', 'trialing', 'past_due')
                    ORDER BY s.created_at DESC, s.id DESC
                    LIMIT 1
                    """,
                    (int(company_id),),
                ).fetchone()
                plan_code = row["code"] if row else None

            layers: list[tuple[str, str | None]] = [("platform", None)]

            if company_id is not None:
                if plan_code:
                    layers.append(("plan", str(plan_code)))

                layers.append(("company", str(int(company_id))))

            for scope_type, scope_id in layers:
                published = self._published(
                    conn, scope_type=scope_type, scope_id=scope_id
                )

                if not published:
                    continue

                tokens = _merge_patch(tokens, self._loads(published["tokens_json"]))
                modules = _merge_patch(modules, self._loads(published["modules_json"]))
                version = int(published["version"])

            company_theme: dict[str, Any] = {}

            if company_id is not None:
                row = conn.execute(
                    "SELECT theme_json FROM company_platform_config "
                    "WHERE company_id = ? LIMIT 1",
                    (int(company_id),),
                ).fetchone()

                if row is not None:
                    company_theme = self._loads(self._column(row, "theme_json"))

        tokens = _merge_patch(tokens, company_theme)

        return {"version": version, "tokens": tokens, "modules": modules}

    @staticmethod
    def visible_modules(
        gate: dict[str, bool], theme_modules: dict[str, Any] | None
    ) -> dict[str, bool]:
        """The operator's gate, narrowed by what the theme hides.

        `gate` is the platform operator's decision and the only thing that can
        turn a module *on*. A theme is consulted for one answer only — whether
        an entry the operator allowed should still be drawn — so publishing a
        theme can hide a module and can never reveal one. The result keeps the
        gate's own flat shape, because that is what the workspace configuration
        endpoint has always answered with and what enforces the gate downstream.
        """
        theme_modules = theme_modules if isinstance(theme_modules, dict) else {}

        narrowed: dict[str, bool] = {}

        for key, allowed in gate.items():
            entry = theme_modules.get(key)
            hidden = isinstance(entry, dict) and entry.get("visible") is False
            narrowed[key] = bool(allowed) and not hidden

        return narrowed

    # ------------------------------------------------------------------
    # The draft lifecycle
    # ------------------------------------------------------------------

    def list_themes(
        self, *, scope_type: str, scope_id: str | None
    ) -> list[dict[str, Any]]:
        """Every theme for one scope: the open draft and the version history."""
        scope_type, scope_id = self._scope(scope_type, scope_id)

        with database_manager.control() as conn:
            rows = conn.execute(
                """
                SELECT * FROM ui_themes
                WHERE scope_type = ? AND scope_id IS ?
                ORDER BY version DESC, id DESC
                LIMIT ?
                """,
                (scope_type, scope_id, self.MAX_THEMES),
            ).fetchall()

        return [self.serialize(dict(row)) for row in rows]

    def get_theme(self, *, theme_id: int) -> dict[str, Any]:
        with database_manager.control() as conn:
            row = conn.execute(
                "SELECT * FROM ui_themes WHERE id = ? LIMIT 1", (int(theme_id),)
            ).fetchone()

        if not row:
            raise PlatformUiNotFound(f"No theme with id {theme_id}.")

        return dict(row)

    def create_draft(
        self,
        *,
        scope_type: str,
        scope_id: str | None,
        tokens: dict[str, Any] | None = None,
        modules: dict[str, Any] | None = None,
        created_by: int | None = None,
    ) -> dict[str, Any]:
        """Open a draft for one scope, starting from what that scope has published.

        Starting from an empty patch instead would lose work at publish time:
        publishing replaces the scope's published row, so any earlier key this
        draft happened not to touch would silently revert the moment it went
        live.
        """
        scope_type, scope_id = self._scope(scope_type, scope_id)

        token_patch = _validate_tokens_patch(tokens or {})
        module_patch = _validate_modules_patch(modules or {})

        now = utc_now_iso()

        with database_manager.control() as conn:
            published = self._published(conn, scope_type=scope_type, scope_id=scope_id)

            base_tokens = self._loads(published["tokens_json"]) if published else {}
            base_modules = self._loads(published["modules_json"]) if published else {}

            cursor = conn.execute(
                """
                INSERT INTO ui_themes (
                    scope_type, scope_id, version, tokens_json, modules_json,
                    status, created_by, created_at
                )
                VALUES (?, ?, 0, ?, ?, 'draft', ?, ?)
                """,
                (
                    scope_type,
                    scope_id,
                    self._dumps(_merge_patch(base_tokens, token_patch)),
                    self._dumps(_merge_patch(base_modules, module_patch)),
                    int(created_by) if created_by is not None else None,
                    now,
                ),
            )
            theme_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_theme(theme_id=theme_id)

    def update_draft(
        self,
        *,
        theme_id: int,
        tokens: dict[str, Any] | None = None,
        modules: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Merge one more change into an open draft.

        `None` means "not mentioned in this request" and leaves that half of the
        draft alone; `{}` means "mentioned and empty" and changes nothing
        either. Theme Studio saves one control at a time, so almost every call
        here carries a single key.
        """
        theme = self.get_theme(theme_id=theme_id)

        if theme["status"] != "draft":
            raise PlatformUiError(
                "Only a draft can be edited. Publishing creates a new version "
                "rather than rewriting one."
            )

        merged_tokens = self._loads(theme["tokens_json"])
        merged_modules = self._loads(theme["modules_json"])

        if tokens is not None:
            merged_tokens = _merge_patch(merged_tokens, _validate_tokens_patch(tokens))

        if modules is not None:
            merged_modules = _merge_patch(
                merged_modules, _validate_modules_patch(modules)
            )

        with database_manager.control() as conn:
            conn.execute(
                "UPDATE ui_themes SET tokens_json = ?, modules_json = ? WHERE id = ?",
                (
                    self._dumps(merged_tokens),
                    self._dumps(merged_modules),
                    int(theme_id),
                ),
            )
            conn.commit()

        return self.get_theme(theme_id=theme_id)

    def publish(
        self,
        *,
        theme_id: int,
        actor_user_id: int | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Turn a draft into the next numbered version of its scope.

        The version it replaces is archived rather than deleted, which is what
        makes `restore` possible and what makes the history page mean anything.
        The audit row carries the reason and both sides of the change, because
        "the whole platform changed colour" is a question somebody asks weeks
        later.
        """
        theme = self.get_theme(theme_id=theme_id)

        if theme["status"] != "draft":
            raise PlatformUiError("Only a draft can be published.")

        now = utc_now_iso()

        with database_manager.control() as conn:
            previous = self._published(
                conn, scope_type=theme["scope_type"], scope_id=theme["scope_id"]
            )
            version = (int(previous["version"]) if previous else 0) + 1

            if previous:
                conn.execute(
                    "UPDATE ui_themes SET status = 'archived' WHERE id = ?",
                    (int(previous["id"]),),
                )

            conn.execute(
                """
                UPDATE ui_themes
                SET status = 'published', version = ?, published_at = ?
                WHERE id = ?
                """,
                (version, now, int(theme_id)),
            )

            # In the same transaction as the change it describes, so the record
            # and the fact can never disagree.
            platform_service.record_audit(
                action="ui_theme.published",
                actor_user_id=actor_user_id,
                company_id=(
                    int(theme["scope_id"])
                    if theme["scope_type"] == "company" and theme["scope_id"]
                    else None
                ),
                target_type="ui_theme",
                target_id=theme_id,
                data={
                    "scope_type": theme["scope_type"],
                    "scope_id": theme["scope_id"],
                    "version": version,
                    "replaced_version": (
                        int(previous["version"]) if previous else None
                    ),
                    "reason": reason,
                    "tokens": self._loads(theme["tokens_json"]),
                    "modules": self._loads(theme["modules_json"]),
                },
                conn=conn,
            )

            conn.commit()

        return self.get_theme(theme_id=theme_id)

    def restore(
        self, *, theme_id: int, created_by: int | None = None
    ) -> dict[str, Any]:
        """Reopen an archived version as a new draft.

        A copy rather than a status change: the archived row is the record of
        what the platform actually looked like between two dates, and editing it
        back into life would erase that.

        The copy is taken verbatim, so restoring a version restores exactly that
        version and not a merge of it with whatever is live now — which is what
        an administrator reaching for "restore" is asking for.
        """
        theme = self.get_theme(theme_id=theme_id)

        if theme["status"] != "archived":
            raise PlatformUiError(
                "Only an archived version can be restored into a new draft."
            )

        now = utc_now_iso()

        with database_manager.control() as conn:
            cursor = conn.execute(
                """
                INSERT INTO ui_themes (
                    scope_type, scope_id, version, tokens_json, modules_json,
                    status, created_by, created_at
                )
                VALUES (?, ?, 0, ?, ?, 'draft', ?, ?)
                """,
                (
                    theme["scope_type"],
                    theme["scope_id"],
                    theme["tokens_json"],
                    theme["modules_json"],
                    int(created_by) if created_by is not None else None,
                    now,
                ),
            )
            theme_id = int(cursor.lastrowid)
            conn.commit()

        return self.get_theme(theme_id=theme_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def serialize(self, theme: dict[str, Any]) -> dict[str, Any]:
        """One theme as the API answers it: patches parsed, no raw JSON."""
        theme = dict(theme)
        theme["tokens"] = self._loads(theme.pop("tokens_json", None))
        theme["modules"] = self._loads(theme.pop("modules_json", None))
        return theme

    @staticmethod
    def _scope(scope_type: str, scope_id: str | None) -> tuple[str, str | None]:
        """Normalise a scope, refusing the combinations that have no meaning."""
        scope_type = str(scope_type)

        if scope_type not in SCOPE_TYPES:
            raise PlatformUiError(
                f"scope_type must be one of {', '.join(SCOPE_TYPES)}."
            )

        if scope_type == "platform":
            # A platform theme reaches everybody; an id would suggest it reaches
            # one of them.
            if scope_id not in (None, ""):
                raise PlatformUiError("The platform scope does not take a scope_id.")

            return scope_type, None

        if not scope_id:
            raise PlatformUiError(f'scope_id is required for scope_type "{scope_type}".')

        scope_id = str(scope_id).strip()

        if len(scope_id) > MAX_BRANDING_VALUE:
            raise PlatformUiError("scope_id is too long.")

        if scope_type == "company" and not scope_id.isdigit():
            raise PlatformUiError("A company scope_id is a company id.")

        return scope_type, scope_id

    @staticmethod
    def _published(conn: Any, *, scope_type: str, scope_id: str | None) -> Any:
        return conn.execute(
            """
            SELECT * FROM ui_themes
            WHERE scope_type = ? AND scope_id IS ? AND status = 'published'
            ORDER BY version DESC, id DESC
            LIMIT 1
            """,
            (scope_type, scope_id),
        ).fetchone()

    @staticmethod
    def _column(row: Any, name: str) -> Any:
        """A column that may predate its migration, read without raising."""
        try:
            return row[name]
        except (IndexError, KeyError):
            return None

    @staticmethod
    def _loads(raw: Any) -> dict[str, Any]:
        if not raw:
            return {}

        if isinstance(raw, dict):
            return raw

        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("Ignoring an unreadable theme patch.")
            return {}

        return value if isinstance(value, dict) else {}

    @staticmethod
    def _dumps(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False)


platform_ui_service = PlatformUiService()
