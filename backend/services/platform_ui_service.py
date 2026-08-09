import copy
import json
from datetime import datetime, timezone
from typing import Any

from config.settings import config
from database.database import db


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


SCOPE_TYPES = ("platform", "plan", "company")

# The nav modules a theme's modules_json can toggle/reorder/relabel.
# Keys match the groups in CLAUDE_CODE_UI_IMPLEMENTATION.md §2.
MODULE_KEYS = (
    "dashboard", "conversations", "notifications", "tasks", "appointments", "team_chat",
    "customers", "broadcast", "calls", "dialer",
    "ai_teaching", "test_ai", "saved_replies", "reply_flows",
    "community", "publish", "comments", "catalogue", "analytics",
    "company_settings", "roles_permissions", "settings", "platform_admin", "theme_studio",
)

# Per-company Platform Admin "Modules" toggles (companies.module_*_enabled
# columns) overlaid onto the resolved theme so the sidebar hides a module
# the moment a super admin suspends it for that company — previously these
# columns were enforced by the API routes (403) but the nav item stayed
# visible, which read as a broken page rather than a disabled module.
COMPANY_MODULE_FLAG_MAP = {
    "appointments": ("appointments",),
    "scheduler": ("publish",),
    "catalogue": ("catalogue",),
    "team_chat": ("team_chat",),
    "comments": ("comments",),
}

ALLOWED_FONTS = ("Inter", "Cormorant Garamond", "Lora", "IBM Plex Sans", "Manrope", "Cairo")
ALLOWED_MODES = ("light", "dark")
ALLOWED_RAILS = ("paper", "ink", "accent")
ALLOWED_BUTTONS = ("outline", "soft", "solid")
ALLOWED_SHADOWS = ("none", "sm", "md")
ALLOWED_DIRECTIONS = ("auto", "ltr", "rtl")

NUMERIC_RANGES = {
    ("type", "baseSize"): (12, 18),
    ("type", "headingScale"): (0.85, 1.25),
    ("shape", "radius"): (0, 24),
    ("layout", "density"): (0.75, 1.2),
    ("layout", "railWidth"): (180, 300),
}


# These mirror T-ZONE's actual current hand-picked look (see
# frontend/src/styles/theme.css's hard-coded var() fallbacks) — NOT the
# illustrative sample values in CLAUDE_CODE_THEME_SPEC.md §2, which only
# demonstrate the token shape. Resolving with zero published themes
# anywhere must be visually identical to today's app; only an admin
# explicitly publishing a theme should ever change how it looks.
DEFAULT_TOKENS: dict[str, Any] = {
    "color": {"accent": "#4F63F0", "accent2": "#22C07D", "mode": "light", "rail": "paper"},
    "type": {"headingFont": "Inter", "bodyFont": "Inter", "baseSize": 15, "headingScale": 1.0},
    "shape": {"radius": 16, "buttons": "solid", "cardFill": True, "shadow": "sm"},
    "layout": {"density": 1.0, "railWidth": 236, "direction": "auto"},
}

DEFAULT_MODULES: dict[str, Any] = {
    key: {"visible": True, "label": None, "order": index}
    for index, key in enumerate(MODULE_KEYS)
}

DEFAULT_BRAND: dict[str, Any] = {"name": "T-ZONE", "logoUrl": "/tzone-logo.png"}


class PlatformUiValidationError(ValueError):
    pass


def _validate_tokens_patch(candidate: dict[str, Any]) -> dict[str, Any]:
    """Validates a *sparse* tokens patch — only the keys a draft actually
    overrides, not a full snapshot. Each theme row stores just its own
    diff so resolve_config can shallow-merge platform -> plan -> company
    at read time without a lower layer's untouched keys clobbering a
    higher layer's values (storing full merged snapshots made every new
    draft silently reset any key it didn't set back to the bundled
    default instead of inheriting the resolved parent layer)."""
    if not isinstance(candidate, dict):
        raise PlatformUiValidationError("tokens must be an object.")

    cleaned: dict[str, dict[str, Any]] = {}
    for section, values in candidate.items():
        if section not in DEFAULT_TOKENS:
            raise PlatformUiValidationError(f'Unknown token section "{section}".')
        if not isinstance(values, dict):
            raise PlatformUiValidationError(f'Token section "{section}" must be an object.')
        cleaned_section: dict[str, Any] = {}
        for key, value in values.items():
            if key not in DEFAULT_TOKENS[section]:
                raise PlatformUiValidationError(f'Unknown token "{section}.{key}".')
            cleaned_section[key] = value
        if cleaned_section:
            cleaned[section] = cleaned_section

    color = cleaned.get("color", {})
    if "mode" in color and color["mode"] not in ALLOWED_MODES:
        raise PlatformUiValidationError(f'color.mode must be one of {ALLOWED_MODES}.')
    if "rail" in color and color["rail"] not in ALLOWED_RAILS:
        raise PlatformUiValidationError(f'color.rail must be one of {ALLOWED_RAILS}.')
    for hex_key in ("accent", "accent2"):
        if hex_key in color:
            value = str(color[hex_key])
            if not (value.startswith("#") and len(value) in (4, 7)):
                raise PlatformUiValidationError(f'color.{hex_key} must be a hex colour.')

    type_tokens = cleaned.get("type", {})
    for font_key in ("headingFont", "bodyFont"):
        if font_key in type_tokens and type_tokens[font_key] not in ALLOWED_FONTS:
            raise PlatformUiValidationError(f'{font_key} must be one of {ALLOWED_FONTS}.')

    shape = cleaned.get("shape", {})
    if "buttons" in shape and shape["buttons"] not in ALLOWED_BUTTONS:
        raise PlatformUiValidationError(f'shape.buttons must be one of {ALLOWED_BUTTONS}.')
    if "shadow" in shape and shape["shadow"] not in ALLOWED_SHADOWS:
        raise PlatformUiValidationError(f'shape.shadow must be one of {ALLOWED_SHADOWS}.')
    if "cardFill" in shape and not isinstance(shape["cardFill"], bool):
        raise PlatformUiValidationError("shape.cardFill must be a boolean.")

    layout = cleaned.get("layout", {})
    if "direction" in layout and layout["direction"] not in ALLOWED_DIRECTIONS:
        raise PlatformUiValidationError(f'layout.direction must be one of {ALLOWED_DIRECTIONS}.')

    for (section, key), (low, high) in NUMERIC_RANGES.items():
        if key in cleaned.get(section, {}):
            value = cleaned[section][key]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not (low <= value <= high):
                raise PlatformUiValidationError(f'{section}.{key} must be a number between {low} and {high}.')

    return cleaned


def _validate_modules_patch(candidate: dict[str, Any]) -> dict[str, Any]:
    """Validates a sparse modules patch — same diff-not-snapshot model
    as tokens (see _validate_tokens_patch)."""
    if not isinstance(candidate, dict):
        raise PlatformUiValidationError("modules must be an object.")

    cleaned: dict[str, dict[str, Any]] = {}
    for key, entry in candidate.items():
        if key not in MODULE_KEYS:
            raise PlatformUiValidationError(f'Unknown module "{key}".')
        if not isinstance(entry, dict):
            raise PlatformUiValidationError(f'Module "{key}" must be an object.')
        cleaned_entry: dict[str, Any] = {}
        if "visible" in entry:
            if not isinstance(entry["visible"], bool):
                raise PlatformUiValidationError(f'Module "{key}".visible must be a boolean.')
            cleaned_entry["visible"] = entry["visible"]
        if "label" in entry:
            if entry["label"] is not None and not isinstance(entry["label"], str):
                raise PlatformUiValidationError(f'Module "{key}".label must be a string or null.')
            cleaned_entry["label"] = entry["label"]
        if "order" in entry:
            if not isinstance(entry["order"], int) or isinstance(entry["order"], bool):
                raise PlatformUiValidationError(f'Module "{key}".order must be an integer.')
            cleaned_entry["order"] = entry["order"]
        if cleaned_entry:
            cleaned[key] = cleaned_entry
    return cleaned


def _merge_tokens_patch(accumulator: dict[str, Any], patch: dict[str, Any] | None) -> dict[str, Any]:
    """Shallow-merges a sparse tokens patch onto the running resolved
    object, one section at a time (platform -> plan -> company; later
    wins per key, untouched keys keep the prior layer's value)."""
    if not patch:
        return accumulator
    merged = copy.deepcopy(accumulator)
    for section, values in patch.items():
        merged.setdefault(section, {}).update(values)
    return merged


def _merge_modules_patch(accumulator: dict[str, Any], patch: dict[str, Any] | None) -> dict[str, Any]:
    if not patch:
        return accumulator
    merged = copy.deepcopy(accumulator)
    for key, entry in patch.items():
        merged.setdefault(key, {}).update(entry)
    return merged


class PlatformUiService:
    def ensure_schema(self) -> None:
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ui_themes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_type TEXT NOT NULL,
                    scope_id TEXT,
                    version INTEGER NOT NULL DEFAULT 0,
                    tokens_json TEXT NOT NULL,
                    modules_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    created_by INTEGER,
                    created_at TEXT NOT NULL,
                    published_at TEXT,
                    FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ui_themes_scope "
                "ON ui_themes(scope_type, scope_id, status)"
            )
            conn.commit()

    # ---- Resolution (read path used by every customer session) --------

    def resolve_config(self, *, company_id: int | None) -> dict[str, Any]:
        tokens = copy.deepcopy(DEFAULT_TOKENS)
        modules = copy.deepcopy(DEFAULT_MODULES)
        version = 0

        layers: list[tuple[str, str | None]] = [("platform", None)]
        plan_code = None
        if company_id is not None:
            with db.connect() as conn:
                row = conn.execute(
                    """
                    SELECT p.code AS plan_code
                    FROM subscriptions s
                    JOIN plans p ON p.id = s.plan_id
                    WHERE s.company_id = ? AND s.status IN ('active', 'trialing', 'past_due')
                    ORDER BY s.created_at DESC LIMIT 1
                    """,
                    (company_id,),
                ).fetchone()
            plan_code = row["plan_code"] if row else None
            if plan_code:
                layers.append(("plan", plan_code))
            layers.append(("company", str(company_id)))

        for scope_type, scope_id in layers:
            published = self._latest_published(scope_type=scope_type, scope_id=scope_id)
            if not published:
                continue
            tokens = _merge_tokens_patch(tokens, json.loads(published["tokens_json"]))
            modules = _merge_modules_patch(modules, json.loads(published["modules_json"]))
            version = published["version"]

        if company_id is not None:
            from backend.services.platform_admin_service import platform_admin_service

            for flag, module_keys in COMPANY_MODULE_FLAG_MAP.items():
                if not platform_admin_service.is_module_enabled(company_id=company_id, module=flag):
                    for key in module_keys:
                        modules.setdefault(key, {})["visible"] = False

        return {
            "version": version,
            "tokens": tokens,
            "modules": modules,
            "brand": copy.deepcopy(DEFAULT_BRAND),
        }

    def _latest_published(self, *, scope_type: str, scope_id: str | None) -> dict[str, Any] | None:
        with db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM ui_themes
                WHERE scope_type = ? AND scope_id IS ? AND status = 'published'
                ORDER BY version DESC LIMIT 1
                """,
                (scope_type, scope_id),
            ).fetchone()
        return dict(row) if row else None

    # ---- Draft lifecycle ------------------------------------------------

    def list_themes(self, *, scope_type: str, scope_id: str | None) -> list[dict[str, Any]]:
        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM ui_themes
                WHERE scope_type = ? AND scope_id IS ?
                ORDER BY version DESC, created_at DESC
                """,
                (scope_type, scope_id),
            ).fetchall()
        return [self.serialize(dict(row)) for row in rows]

    def get_theme(self, *, theme_id: int) -> dict[str, Any]:
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM ui_themes WHERE id = ?", (theme_id,)).fetchone()
        if not row:
            raise KeyError("Theme not found")
        return dict(row)

    def create_draft(
        self, *, scope_type: str, scope_id: str | None, tokens: dict[str, Any] | None,
        modules: dict[str, Any] | None, created_by: int | None,
    ) -> dict[str, Any]:
        if scope_type not in SCOPE_TYPES:
            raise PlatformUiValidationError(f"scope_type must be one of {SCOPE_TYPES}.")
        if scope_type == "platform" and scope_id is not None:
            raise PlatformUiValidationError("Platform scope does not take a scope_id.")
        if scope_type in ("plan", "company") and not scope_id:
            raise PlatformUiValidationError(f'scope_id is required for scope_type "{scope_type}".')

        # A new draft must start from whatever this scope currently has
        # published, not from an empty patch — otherwise publishing it
        # replaces the published row wholesale and any earlier published
        # key this draft doesn't touch (e.g. color.accent from a prior
        # publish) is silently lost the moment this one goes live.
        published = self._latest_published(scope_type=scope_type, scope_id=scope_id)
        base_tokens = json.loads(published["tokens_json"]) if published else {}
        base_modules = json.loads(published["modules_json"]) if published else {}

        cleaned_tokens = _merge_tokens_patch(base_tokens, _validate_tokens_patch(tokens or {}))
        cleaned_modules = _merge_modules_patch(base_modules, _validate_modules_patch(modules or {}))

        now = utc_now_iso()
        with db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO ui_themes
                    (scope_type, scope_id, version, tokens_json, modules_json, status, created_by, created_at)
                VALUES (?, ?, 0, ?, ?, 'draft', ?, ?)
                """,
                (scope_type, scope_id, json.dumps(cleaned_tokens), json.dumps(cleaned_modules), created_by, now),
            )
            conn.commit()
            theme_id = cursor.lastrowid
        return self.get_theme(theme_id=theme_id)

    def update_draft(self, *, theme_id: int, tokens: dict[str, Any] | None, modules: dict[str, Any] | None) -> dict[str, Any]:
        theme = self.get_theme(theme_id=theme_id)
        if theme["status"] != "draft":
            raise PlatformUiValidationError("Only a draft can be edited — publish creates a new version instead.")

        current_tokens = json.loads(theme["tokens_json"])
        current_modules = json.loads(theme["modules_json"])
        cleaned_tokens = _merge_tokens_patch(current_tokens, _validate_tokens_patch(tokens)) if tokens is not None else current_tokens
        cleaned_modules = _merge_modules_patch(current_modules, _validate_modules_patch(modules)) if modules is not None else current_modules

        with db.connect() as conn:
            conn.execute(
                "UPDATE ui_themes SET tokens_json = ?, modules_json = ? WHERE id = ?",
                (json.dumps(cleaned_tokens), json.dumps(cleaned_modules), theme_id),
            )
            conn.commit()
        return self.get_theme(theme_id=theme_id)

    def publish(self, *, theme_id: int, actor_user_id: int | None, reason: str | None) -> dict[str, Any]:
        theme = self.get_theme(theme_id=theme_id)
        if theme["status"] != "draft":
            raise PlatformUiValidationError("Only a draft can be published.")

        previous = self._latest_published(scope_type=theme["scope_type"], scope_id=theme["scope_id"])
        new_version = (previous["version"] if previous else 0) + 1
        now = utc_now_iso()

        with db.connect() as conn:
            if previous:
                conn.execute("UPDATE ui_themes SET status = 'archived' WHERE id = ?", (previous["id"],))
            conn.execute(
                "UPDATE ui_themes SET status = 'published', version = ?, published_at = ? WHERE id = ?",
                (new_version, now, theme_id),
            )
            conn.execute(
                """
                INSERT INTO audit_logs
                    (workspace_id, company_id, user_id, action, entity_type, entity_id, old_values_json, new_values_json, created_at)
                VALUES (?, ?, ?, 'ui_theme_published', 'ui_theme', ?, ?, ?, ?)
                """,
                (
                    config.DEFAULT_WORKSPACE_ID,
                    int(theme["scope_id"]) if theme["scope_type"] == "company" and theme["scope_id"] else None,
                    actor_user_id,
                    str(theme_id),
                    json.dumps({
                        "scope_type": theme["scope_type"], "scope_id": theme["scope_id"],
                        "version": previous["version"] if previous else None,
                        "tokens": json.loads(previous["tokens_json"]) if previous else None,
                        "modules": json.loads(previous["modules_json"]) if previous else None,
                    }),
                    json.dumps({
                        "scope_type": theme["scope_type"], "scope_id": theme["scope_id"],
                        "version": new_version, "reason": reason,
                        "tokens": json.loads(theme["tokens_json"]), "modules": json.loads(theme["modules_json"]),
                    }),
                    now,
                ),
            )
            conn.commit()
        return self.get_theme(theme_id=theme_id)

    def restore(self, *, theme_id: int, created_by: int | None) -> dict[str, Any]:
        theme = self.get_theme(theme_id=theme_id)
        if theme["status"] != "archived":
            raise PlatformUiValidationError("Only an archived version can be restored into a new draft.")

        now = utc_now_iso()
        with db.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO ui_themes
                    (scope_type, scope_id, version, tokens_json, modules_json, status, created_by, created_at)
                VALUES (?, ?, 0, ?, ?, 'draft', ?, ?)
                """,
                (theme["scope_type"], theme["scope_id"], theme["tokens_json"], theme["modules_json"], created_by, now),
            )
            conn.commit()
            new_id = cursor.lastrowid
        return self.get_theme(theme_id=new_id)

    def serialize(self, theme: dict[str, Any]) -> dict[str, Any]:
        theme = dict(theme)
        theme["tokens"] = json.loads(theme.pop("tokens_json"))
        theme["modules"] = json.loads(theme.pop("modules_json"))
        return theme


platform_ui_service = PlatformUiService()
