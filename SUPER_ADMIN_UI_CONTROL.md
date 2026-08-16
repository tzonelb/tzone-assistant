# The Super Admin control plane

The platform operator runs the business of the platform — creating companies,
suspending them, assigning plans, deciding which modules a company sees —
without being able to read any company's customer data.

That is not a convention. Each company's database is sealed with its own key,
and an employee proves possession of the workspace code at sign-in. If the
operator's console could list a company's conversations, the per-company
encryption would only be protecting customers from a stolen disk.

## Two credentials, never interchangeable

| | Customer workspace | Super Admin console |
|---|---|---|
| Sign in at | `/login` | `/superadmin/login` |
| Needs a workspace code | yes — it unseals the database | no — no company database is opened |
| Token scope | `company` | `platform` |
| Browser storage key | `tzone_access_token` | `tzone_platform_token` |
| API | every `/api/*` module route | `/api/platform/*` only |
| Dependency | `get_current_user` | `get_platform_admin` |

A platform token is refused by every customer endpoint, and a company token is
refused by every console endpoint. Both credentials can sit in one browser at
once; signing out of either leaves the other alone.

A super admin who genuinely needs to work inside a company signs into that
company at `/login` with its workspace code, like anyone else. Holding one
company's code has never granted reach into a second.

## What the console can see about a company

Counts and file size — `company_statistics` runs `COUNT(*)` over a fixed table
map and returns integers. It is the only place in `backend/services/platform_service.py`
that opens a tenant database, and a test asserts that, so an edit which starts
returning rows fails rather than leaks.

## Module, branding and layout control

`PUT /api/platform/companies/{id}/config` stores three things per company:
module switches, branding tokens and layout flags. The valid keys are
`PLATFORM_MODULES`, `BRANDING_FIELDS` and `LAYOUT_FLAGS` in
`backend/services/platform_service.py`; an unknown key is refused rather than
stored, because a stored typo reads back like a decision that was applied and
disables nothing.

The switches are enforced, not cosmetic:

- `backend/services/module_access.require_module` guards each module's router
  in `main.py`. Turning Catalogue off for a company closes `/api/catalogue` for
  that company and for nobody else.
- The customer app reads the same decision from `GET /api/platform-ui/config`
  and hides the navigation entry, so an employee is not shown a door that opens
  onto a 403. `frontend/src/contexts/WorkspaceConfigContext.jsx` also applies
  the branding tokens.
- `GET /api/company-settings/modules` reports the same decision and locks it.
  It used to carry five switches of its own that nothing read, so a company
  could turn Appointments "off" and keep using it.

A module absent from a company's stored config is **on**. Defaulting to off
would silently disable every module of every existing company the first time
the platform ships a new one.

## The console itself

`frontend/src/superadmin/` — a separate shell, a separate visual identity and a
separate fetch layer with its own token key. It imports nothing from the
customer application's contexts, layout or API client, and is code-split so the
customer app does not download it.

Screens: sign-in, companies, company detail (status, statistics, plan,
workspace-code rotation, module/branding/layout editor), new company, platform
administrators, audit log, health.

## Locked customer UI

- T-ZONE logo and brand colours remain the default, overridden per company only
  by the branding fields above.
- Dashboard keeps normal page scrolling.
- Conversations and Comments use viewport-height workspaces with internal
  scrolling only.
- Chat composer remains a single row: attachments, image, voice, wide input,
  circular send button.
