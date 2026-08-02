# T-ZONE — Theme Studio (Super Admin UI Control Plane)

Implementation order for Claude Code, working in `C:\PROJECTS\tzone-assistant`.
This spec turns the mockup screen **Platform Admin → Theme Studio** into a real feature.
Read `SUPER_ADMIN_UI_CONTROL.md` first — this extends the foundation it describes.

## 0. Non-negotiables (do not break)

- Backend stays the source of truth for permissions. Theme config is presentation only; it must never gate data.
- Only `is_super_admin` may write platform-scope themes. A company `owner` may write **their own tenant override** and nothing else.
- The customer UI must still render if the endpoint fails — fall back to the bundled defaults, never to a blank screen.
- Never modify the Messenger webhook path.
- Every publish writes an audit-log row (actor, scope, version, diff).

## 1. Data model

New table `ui_themes`:

| column | type | notes |
| --- | --- | --- |
| `id` | int pk | |
| `scope_type` | text | `platform` \| `plan` \| `company` |
| `scope_id` | text null | plan code or company id; null for platform |
| `version` | int | increments per scope |
| `tokens_json` | text | the token object below |
| `modules_json` | text | module visibility + menu order/labels |
| `status` | text | `draft` \| `published` \| `archived` |
| `created_by` | int fk users | |
| `created_at` | text | UTC ISO |
| `published_at` | text null | |

Resolution order at read time: `platform` → `plan` → `company`, shallow-merged in that order (later wins per key).

## 2. Token object (`tokens_json`)

```json
{
  "color": {
    "accent": "#1b9be0",
    "accent2": "#3fb552",
    "mode": "light",
    "rail": "paper"
  },
  "type": {
    "headingFont": "Cormorant Garamond",
    "bodyFont": "Lora",
    "baseSize": 15,
    "headingScale": 1.0
  },
  "shape": {
    "radius": 4,
    "buttons": "outline",
    "cardFill": false,
    "shadow": "sm"
  },
  "layout": {
    "density": 1.0,
    "railWidth": 222,
    "direction": "auto"
  }
}
```

Allowed values — `mode`: `light|dark`; `rail`: `paper|ink|accent`; `buttons`: `outline|soft|solid`;
`shadow`: `none|sm|md`; `direction`: `auto|ltr|rtl` (`auto` = follow interface language).
Numeric ranges: `baseSize` 12–18, `headingScale` 0.85–1.25, `radius` 0–24, `density` 0.75–1.2, `railWidth` 180–300.
Fonts are a server-side allow-list (`Cormorant Garamond`, `Lora`, `IBM Plex Sans`, `Manrope`, `Cairo`) so a bad value can't break rendering; reject anything else with 422.

Derived at render time, not stored: the accent 100–900 ramp (`color-mix` against white/black), `--shadow-*`, and the `--space-*` scale (base scale × `density`).

## 3. API

```
GET    /api/platform-ui/config          → resolved config for the caller's company (public to authenticated users)
GET    /api/platform-ui/themes          → list versions for a scope   (super admin; owner for own company)
POST   /api/platform-ui/themes          → create draft                (body: scope_type, scope_id, tokens, modules)
PATCH  /api/platform-ui/themes/{id}     → update draft
POST   /api/platform-ui/themes/{id}/publish   → publish, archive previous, bump version
POST   /api/platform-ui/themes/{id}/restore   → clone an archived version into a new draft
```

`GET /api/platform-ui/config` response:

```json
{ "version": 14, "tokens": { ... }, "modules": { ... }, "brand": { "name": "T-ZONE", "logoUrl": "/uploads/brand/tzone.png" } }
```

Cache it with an ETag; the frontend revalidates on load and after every publish.

## 4. Frontend — applying the theme

1. `frontend/src/config/platformDefaults.js` holds the same shape as the endpoint and is the offline fallback.
2. A `ThemeProvider` fetches `/api/platform-ui/config` once at app start, then writes every token onto
   `document.documentElement.style` as CSS custom properties (`--color-accent`, `--font-heading`, `--radius-md`,
   `--space-*`, `--tz-rail`, `--tz-rail-width`, `--tz-btn-bg`, `--tz-card-bg`, …).
3. `frontend/src/styles/theme.css` keeps its `--tz-*` names but every value becomes `var(--<token>, <current default>)`,
   so nothing breaks before the first fetch.
4. Components must stop hard-coding hexes, radii and px spacing — they read the variables. Grep for `#` in
   `src/pages/**/*.css` and convert; this is the bulk of the work and must be finished before publish is enabled.
5. Module visibility filters both the sidebar list in `components/layout/Sidebar.jsx` **and** the route table in
   `App.jsx` (a hidden module must 404, not just disappear from the menu).

## 5. Admin screen

Route `/platform-admin/theme-studio`, super-admin only, laid out as in the mockup:

- Left: sections — Brand & colour · Typography · Shape & elevation · Layout & density · Modules & menu · Scope & publish; plus presets (Classical, T-ZONE Modern, Console Dark, Arabic First).
- Centre: the controls for the active section, bound directly to the draft token object (debounced 150 ms).
- Right: a live preview pane rendering real components (conversation header, stat card, buttons, table) under the draft tokens, plus **Save draft** / **Publish**.
- Publishing shows a diff of changed tokens and asks for a one-line reason (stored in the audit log).

## 6. Acceptance

- Changing accent in the studio updates every tint, tag, chart bar and hover in the customer UI after publish — no page reload needed for the admin's own preview.
- A tenant override does not leak to other tenants; a platform change reaches tenants that have no override.
- Rollback to any archived version restores the exact previous appearance.
- Turning off a module removes its menu entry and its route for every user in scope.
- With the backend unreachable, the customer UI still renders using `platformDefaults.js`.
- Manual multi-user QA on two tenants before this is called done — build success is not acceptance.
