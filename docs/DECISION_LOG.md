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

## D-012 — A module switched off is absent, not hidden

The Super Admin's module switches were enforced in exactly one place: a
FastAPI dependency on the customer routers. That closed the API and left the
half a customer actually sees wide open.

A company that turned **Catalogue** off got the screen hidden from its own team
and an assistant that went on quoting prices out of the catalogue behind it —
from rows nobody on that team could open to correct. **Tasks** off still opened
tickets into a table the team could not read, while the flow told the customer
a ticket existed. **Knowledge** off still answered out of the base.

So the switch said off and the behaviour stayed on. The code was overruling the
owner, which is backwards: the code is the mechanism, the owner sets the policy.

The rule is now the owner's: **off means absent.** `backend/services/module_gate.py`
is the single answer for every layer, and `module_access` delegates to it — two
sources would eventually disagree, and the disagreement would be invisible
(screen off, assistant on). It carries no FastAPI import, so `core/` and
`channels/` can ask a configuration question without dragging in the web layer,
and it caches per company because the reply path asks once per message.

Gated on the reply path: knowledge, catalogue, tasks, comments, notifications,
scheduler. Each has a regression test, and each test was verified to fail with
the gate removed.

### What is deliberately not gated, and why

`conversations`, `customers`, `channels`, `roles`, `company_settings`,
`dashboard` and `preferences` are the platform's spine, not features. Applying
"absent" to them would mean refusing to store a customer's message, or losing
the record of who a customer is — destroying data the company paid for and
cannot recover when the switch goes back on. Hiding a screen is not consent to
delete what is behind it.

Turning those off keeps its existing meaning: the team cannot reach the module,
and everything already recorded stays intact.

### Failure is open

If the control plane cannot be read, every module reports on. Failing closed
would let one database blip silently strip a thousand companies' assistants of
their knowledge and their catalogue mid-conversation, with nothing in the
switch to explain it. Honouring a switch late is better than honouring one that
was never set.

## D-013 — A switch that shows a decision must make it

Nine reply-policy switches are offered to a company on the AI TEACHING screen,
each with a sentence explaining what it does to a customer. Four worked.

`reply_mode`, `grounded_ai_enabled`, `allow_ai_free_reply`,
`minimum_match_confidence` and `fallback_to_human` were validated on write,
merged across the four scopes, serialised into the model's payload — and never
consulted by anything that decided anything. `grounded_ai_enabled` did not
appear anywhere in the codebase outside the field list that draws its toggle.

So an owner could set "Off keeps it to what you taught it", watch it save, and
get an assistant that went on answering whatever it liked. That is worse than
having no control: the owner believes the guardrail is on.

The rules now live in `core/reply_decision.py`, apart from the engine and
touching no database, session, model or request, so nine interacting switches
can be asserted directly rather than inferred from a reply.

### Consequence to be aware of before launch

The shipped policy in `config/response_policy.json` is `reply_mode: grounded_ai`
with `allow_ai_free_reply: false` and `minimum_match_confidence: 0.62`. Enforced,
that means **a company with an empty knowledge base answers every message with
the safe fallback** — it has nothing confirmed to answer from, so it says so.

That is the documented intent and the safe direction: an assistant that invents
answers about a business it knows nothing about is the larger risk. It is also
a real change from the previous behaviour, where every message reached the model
with a free hand regardless of the switches. A company that wants the old
behaviour turns `allow_ai_free_reply` on; the platform default can be changed in
one value in that file.

### The one combination with no consistent reading

`allow_ai_free_reply` off with `fallback_to_human` off, on an unmatched message:
"keep it to what you taught it" and "answer anyway instead of escalating" cannot
both hold. The content switch wins — no invented answer — and
`fallback_to_human` keeps its own meaning by not routing to a human. The other
precedence would let a switch about *who* answers silently disable a switch
about *what may be said*.

### Out of range is corrupt, not extreme

`minimum_match_confidence` outside 0–1 falls back to the shipped value rather
than clamping. Clamping a stored `9` to `1.0` means nothing ever clears the bar
and the assistant goes silent; clamping a stored `-3` to `0.0` switches the
guardrail off. Both turn bad data into the most extreme setting available,
without a word anywhere.
