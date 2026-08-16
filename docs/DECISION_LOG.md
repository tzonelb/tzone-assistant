# Decision Log

## D-001 — Root `main.py` is canonical

The root `main.py` includes the current route/service lifecycle. `backend/main.py` is legacy/incomplete and should not be used as the official entry point.

## D-002 — Backend is the workflow source of truth

Frontend state cannot grant ownership or permissions. Ownership, read state, AI/human state, and transitions are decided by backend transactions.

## D-003 — Timeline and notifications are separate

Timeline is the conversation audit/activity view. Notifications are employee attention items. AI replies remain in Timeline and do not create bell notifications.

## D-004 — One employee owner

Exactly one employee owns an active human conversation. Stale conflicts return HTTP 409.

## D-005 — Secure Git baseline

The repository was reset to secure baseline commit `b721211...` without secrets/runtime data. Previous contaminated history remains private/archive-only.

## D-006 — Patch 9 status terminology

The deferred implementation is Code Complete, not installed, accepted, merged, or released. No `FINAL` label is used before manual acceptance.

## D-007 — Modular monolith first

Keep the platform as a modular monolith while core workflows stabilize. Introduce services/queues only where operationally justified.

## D-008 — SQLite now, PostgreSQL later

SQLite remains a local/early-stage choice. Production scaling should move to PostgreSQL with formal migrations after conversation stabilization.

## D-009 — The dead second entry point is gone

`backend/main.py` was a second FastAPI application that could not be imported —
it referenced four modules that do not exist — and registered no middleware at
all: no CORS, no security headers. D-001 already said it must not be used as an
entry point. A file that cannot run and would serve the API unprotected if it
somehow did is a hazard rather than a spare, so it was deleted along with
`admin/api/app.py`, a Flask stub that imported a deleted module and has been
superseded by the Super Admin console at `/superadmin`.

`core/profile_loader.py` went with them. It read a single shared
`config/bot_profile.json` and was the last route by which one company's
configuration reached another company's assistant prompt; per-company
`bot_profiles` replaced it and left it with no callers.

## D-010 — Broad exception handlers are a defect, not a convenience

Creating a role raised `IntegrityError` on every attempt for as long as the
feature existed, because the INSERT omitted a NOT NULL column. Nobody noticed,
because an `except Exception` around it answered "A role with this code already
exists" — a plausible sentence that sent every investigation in the wrong
direction.

Catch the exception you mean. In this codebase that also means catching
`sqlcipher.IntegrityError` rather than `sqlite3.IntegrityError`: the SQLCipher
driver has its own exception hierarchy, and `database/manager.py` already
catches `sqlcipher.Error` for the same reason.

The related trap is `INSERT OR IGNORE`, which suppresses a NOT NULL violation
exactly as it suppresses a duplicate. It silently discarded every permission
assigned to a role. Use it only where the constraint being ignored is named and
intended.

## D-011 — An account lock must have a way out that is not waiting

Five failed sign-ins lock an account for everyone, not just for the address
that burned them. On its own that would be a weapon: five requests naming a
known address would disable any employee, from anywhere, for free.

It is safe only because an administrator holding `users.manage` can send a
password-reset link that unlocks it immediately. The lock and the recovery are
one decision and neither ships without the other.

The address throttle is a separate counter with a much higher threshold, and
that gap is load-bearing — an office shares one address, so throttling it as
tightly as an account returns the same collateral damage through another door.
Tests pin the invariant.
