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

## D-014 — Allowances are enforced, and the ceilings they sit inside are not theirs

`plans.max_users`, `max_channel_accounts`, `max_knowledge_items` and
`max_ai_messages` were read in exactly one place — the dashboard, to draw a
number on a card. Nothing refused a sixth user on a five-user plan. There was no
`usage_records` table, so `max_ai_messages` had nothing to count against, and
the four feature flags were read nowhere at all.

Two resolution defects came first, because they made "which plan is this
company on" answer differently depending on who asked:

* **A blank expiry meant expired.** The dashboard's check returned False when
  `expires_at` was empty — while the console's own form says "Leave the date
  empty for a plan that does not expire". Every company deliberately set up not
  to expire read as unsubscribed.
* **An expired subscription was still the plan.** The console's query filtered
  on `status = 'active'` and never looked at `expires_at`, so a subscription
  that ran out last year went on naming the company's plan.

Two answers to one question is how they came to disagree. `plan_service` is the
one answer now and both callers use it.

### Zero is unlimited

Every allowance defaults to 0 in the schema. Read as "none allowed", a plan
created by leaving the fields blank would forbid its customer from adding a
single user — a plan nobody could use, produced by not typing anything. A plan
that genuinely wants to withhold something uses the feature flags, which say
what they mean.

### No subscription does not mean no workspace

A company with no active subscription gets every allowance as unlimited rather
than zero. Zero would mean a billing lapse silently locks a business out of its
own inbox. What a lapse costs is a decision for an operator to make explicitly,
not a side effect of a limit lookup. Features are the exception: one nobody
paid for was never theirs to keep.

### Every limit is guarded on two paths

A limit checked only where a row is created is one anybody can step around by
disabling a member and re-enabling them. Seats and channels are therefore
checked on the create **and** on the status change back to active — and not on
a save that leaves an already-active row active, which would refuse a rename
for occupying the slot it already occupies. Knowledge counts every row, not
only the active ones: an archived item is storage still in use, and counting
only active ones would let a base grow without limit by archiving as it goes.

### Running out of assistant replies switches off the assistant, not the platform

The monthly allowance is checked before the model is called — an allowance that
still pays for the reply it refused is not an allowance. Past it, the customer's
messages are still stored and still in the inbox, and the team answers by hand.
Nothing is said to the customer: what a company pays is not their customer's
business.

### These are not the platform's ceilings

Everything here is a commercial number an operator sets and a customer buys, and
it sits *inside* the hard ceilings that protect the process (body size, events
per request, queue depth, cache size) which no plan can raise. Keeping the two
apart is what stops an Enterprise plan from being able to purchase a value that
stalls the server.

### Every guard fails open

An unreadable control plane allows the write and allows the reply. Refusing
would take a working company's workspace down over a number nobody changed.

## D-015 — The checks that read the codebase itself

Three defects in this repository shared a shape: nothing about the code looked
wrong. An INSERT omitted a `NOT NULL` column and failed on every execution for
the whole life of the feature (D-010). An `INSERT OR IGNORE` silently discarded
every permission assigned to a role. A route with no dependency works, passes
its own tests, and is reachable by anybody who knows the path.

None of these is caught by testing behaviour, because the behaviour under test
is the wrong behaviour. They are caught by reading the source and comparing it
against a rule, which is what `tests/test_schema_contract.py` and
`tests/test_route_exposure.py` do on every run:

* every INSERT supplies every `NOT NULL`-with-no-default column of its table,
  parsed from the schema rather than listed by hand;
* every `INSERT OR IGNORE` names the constraint it means to ignore;
* every route has an authorisation dependency, resolved through local helpers
  (`view_context` and friends never name `require_permission` themselves — what
  they depend on does);
* every exemption carries a reason, and an exemption naming a route that no
  longer exists fails, so the lists cannot rot into cover for a real hole.

Both files were verified by reintroducing the original defects: the D-010
INSERT, and a new route with no dependency. Both were caught.

At the time of writing there are **no** real findings in either — the two
dynamic INSERTs and the eleven identity-only routes were each read and are
correct. The value is not this run; it is the next person adding a route.

## D-016 — One log, in the company's own database, with a security mirror

Of seventeen modules, three wrote any audit at all — and two of those three had
no endpoint to read it back, so the trail existed and nobody could see it.
Nothing recorded a knowledge item being edited, a price being changed, a channel
being connected, a permission being granted, or an employee signing in.

The price one is the sharpest. The assistant quotes catalogue prices to
customers as confirmed facts, so a wrong one is a promise the business then has
to keep — and "who changed that, and from what" had nowhere to be answered. It
is now its own action, separate from an ordinary product edit, so an owner can
filter the log down to exactly the changes that reach a customer's screen.

### The log lives with the company, the mirror with the operator

`activity_log` is a tenant table. It is the company's record of its own
business. `audit_log` in the control plane receives a **mirror of the security
events only** — a sign-in, a lock, a permission change, a channel connected or
disconnected — carrying who, when, from where and a one-line summary, and never
the before/after values. An operator needs to see an attack, which is invisible
in any single company's log when it is spread across a thousand. What a business
sells or teaches its assistant is not part of that.

### The actor's name is copied, not joined

`users` lives in the control plane and the log in the tenant file, and SQLite
cannot join across files. Three existing queries in
`conversation_control_service` try: they `LEFT JOIN users` inside a tenant
connection, match nothing, and render every actor as "System" — so anyone
reading that timeline concludes the platform did it. A snapshot also survives
the employee leaving, and a log that forgets who did something the moment they
resign is not a log.

### A refused sign-in is deliberately unattributed

The email may belong to nobody. Looking it up to decide which company's log to
write to would take a different amount of time depending on whether the account
exists — a timing oracle for enumerating employees, on the one endpoint an
attacker is already pointed at. `authenticate` already spends a dummy password
check to avoid exactly that; spending it back to write a tidier log entry would
be a poor trade. The entry goes to the control plane with no company. The
**lock** that follows names a real user and does reach their company's log,
because an owner who learns about a locked employee from a phone call thinks the
platform is down.

### Writing never fails the thing it records

Every method swallows its own errors. An audit write that can fail a price
update means the price does not change because the note about it could not be
filed. A gap in the log is recoverable; refusing the customer's work is not.

### Three retentions

A change is kept, a read expires sooner because it is by far the highest volume,
and a security event is kept longest because an investigation starts after the
damage. Without the split, recording who read what would bury who changed what
within a year of ordinary use. The maintenance worker applies it.

### The two counters that read zero for ever

`analytics_service` counted `'human_took_over'` and `'assigned_user_changed'`
while the writers wrote `human_takeover` and `assignment_changed`. Neither
matched, so both figures were zero for every company since the report shipped —
and a zero is exactly the kind of wrong answer nobody questions. Both ends now
import one constant.

## D-017 — Two factors for the account that has only one

The Super Admin sign-in is deliberately one factor: an email and a password,
with no workspace code, because a platform administrator belongs to no company
and so has no code to type. It is also the account that suspends companies,
rotates workspace codes and reads the platform audit. One guessed or reused
password is the whole platform.

So enrolment is **mandatory** there and **optional** everywhere else. The
platform decides what protects the platform; a company's owner decides what
protects the company, and can require it of their team by policy — they can see
who has it on.

### TOTP, not SMS

An SMS code travels over a channel a SIM swap takes over, which is a routine
attack against exactly this kind of account. It would also make signing in to
the platform depend on a paid gateway staying up: the failure mode is the
operator locked out of their own console during the incident that made them
need it.

### The session exists before the enrolment does

An unenrolled administrator signs in successfully and can reach three routes:
status, begin, confirm. Everything else answers 403. A dependency that refused
the token outright would leave them with no way to satisfy the requirement — a
locked door on the one account with nobody above it to open one. The permissive
twin `get_platform_admin_enrolling` is used by exactly those three routes and
still demands a platform-scoped token from a super admin.

### What is stored, and what is not

The secret is sealed under the platform master key and bound to the account, so
a database dump yields nothing and a sealed value lifted from one row fails to
decrypt on another. It is a password-equivalent: anyone holding it generates
this account's codes for ever, so a readable copy would mean a dump hands over
the second factor along with the first. Recovery codes are hashed, shown once,
and consumed on use.

`totp_enabled` goes on only after the user proves a code the secret produced.
Turning it on when the secret is issued would lock out anybody whose
authenticator failed to save the QR they had just scanned. Restarting enrolment
issues a new secret and discards the old, so an abandoned attempt cannot be
resumed by somebody who photographed the first QR.

### Verification fails closed

The opposite direction from almost every other guard in this codebase, and
deliberately. The others fail open because refusing would deny a customer work
they are entitled to. Allowing here would admit somebody who has not proved
their second factor. The direction follows the consequence, not a house style.

### A wrong code counts toward the lockout

Otherwise an attacker who already has the password gets an unlimited number of
guesses at six digits.

### Nobody can enrol or remove somebody else's

Every 2FA route acts on the session's own user id and takes none from a
parameter. A permission that let an administrator strip an employee's second
factor would make it a factor two people hold, and so not a second factor.
Removing one's own requires a current code, or anybody at an unlocked screen
could do it in a click — which would make the second factor only as strong as
the session it exists to defend.

### The emergency exit is a server command

`python -m tools.manage_platform reset-totp --email <address>` clears it. An
administrator who has lost both their device and their recovery codes cannot be
helped from inside the product, and without this the platform would be
permanently unadministrable after a lost phone. It is a command rather than an
endpoint because whoever can run it already holds the master key and the
database, so it grants nothing they did not have — an endpoint would be a way to
strip anybody's second factor over the network. The account is left
**unenrolled**, not exempt: the requirement stands at the next sign-in.

## D-018 — A health endpoint that can fail, and a check that runs unasked

`health.py` returned a constant. It said `{"status": "ok"}` whether or not the
master key was loadable, whether or not a single company database could be
opened, and whether or not the disk had run out — so the one endpoint a monitor
watches was the only thing on the platform that could not fail.

`PRAGMA integrity_check` appeared nowhere in the repository, so silent
corruption had nothing looking for it. `upgrade_all_tenants` existed and had no
callers at all — not even at boot — so a release that added a column left every
existing company failing at query time until somebody remembered the CLI.

### Liveness stays a constant, deliberately now

`GET /health` still returns a constant, and that is correct: a liveness probe
that checks its dependencies restarts the process when a database is slow, which
is when restarting helps least — the new process finds the same slow database
and the restart loop becomes the outage. What the platform can actually *do*
lives behind the console at `/api/platform/health/report`, which opens every
company database and reads the host's memory and disk. Neither belongs on an
unauthenticated URL a load balancer polls every few seconds.

### On a timer, not on a click

A corrupt company database discovered when a customer writes in is an incident.
The same corruption found by a sweep at three in the morning is a restore. The
self-check runs every fifteen minutes with the deep integrity check on, logs at
error when anything is not `ok`, and keeps its last result for a dashboard to
read without paying for another pass.

### Three traps, each now a test

* **Suspended companies.** `list_company_ids` filters to active ones, correctly
  — a sweep must not deliver a suspended company's replies. A health check that
  reused it would report a clean platform while a suspended company's file was
  corrupt. `list_all_company_ids` exists for anything that inspects rather than
  serves.
* **The two schema versions disagreed from birth.** `provision_company` recorded
  `company_databases.schema_version` while `_build_tenant_schema` never stamped
  `PRAGMA user_version`, so a fresh company had version N in one place and 0 in
  the other. Nothing read them, so nothing noticed — and a version check would
  have flagged every new company, which is exactly the false alarm that teaches
  an operator to ignore the check.
* **A missing reading is `None`, never zero.** A monitor cannot tell a real zero
  from an absent one, and "0% memory used" reads as healthy.

### The startup upgrade opens only what is behind

`upgrade_all_tenants` opens every company database, which at a thousand
companies is a thousand decryptions before the first request is served.
`upgrade_outdated_tenants` reads the recorded version from the control plane —
one cheap query — and opens only the companies that are behind, suspended ones
included, because a company reinstated after two releases still has to open.

### The server metrics use no new dependency

`psutil` would be one more package to install, pin and audit for a handful of
numbers this platform's Linux host already publishes in `/proc`.

## D-019 — The session token leaves JavaScript's reach

The token lived in `localStorage`, which any script on the page can read. One
cross-site scripting hole anywhere — in a dependency, in a rendered customer
name, in an error message — handed an attacker a working session that outlived
the tab they stole it from.

It is now set as an `httpOnly` cookie, which script cannot read at all. XSS can
still *use* the session by making requests from the page, but it can no longer
take the credential away: the difference between an incident that ends when the
tab closes and one that ends when the token expires.

### The Authorization header still works, and still wins

Removing it would break the CLI, every test, and any integration a customer has
built. The cookie is an additional path. When both arrive, the header wins: a
client that sends a token is naming the session it means, and a stale cookie in
the same browser must not override it.

### CSRF only where a cookie is the credential

A cookie is attached automatically, which is the point and the problem: a form
on another site can make the browser send it. So a cookie-authenticated write
carries a double-submit token — a second, script-readable cookie echoed in a
header. An attacker on another origin can make the browser send the session
cookie but cannot read it to copy the value, because that is what the
same-origin policy prevents.

The check does not touch bearer-token requests (nothing attaches an
`Authorization` header automatically, so they cannot be forged cross-site), safe
methods, or the webhook paths (a provider's callback carries no cookie and is
authenticated by an HMAC over the raw body).

`SameSite=Strict` is set as well. The double-submit token is the belt to its
braces: SameSite is enforced by the browser, and a property enforced only by the
client is one the server cannot rely on.

### The routes that establish a session are exempt, and a test found out why

With a stale cookie in the jar, signing in again was a cookie-authenticated
write and the CSRF check refused it — so a user whose session had expired, or
who wanted to sign in as somebody else, was told to reload a page that would
fail identically. Requiring the *old* session's token in order to replace it is
circular.

It is safe because nothing on those paths reads the existing session: whatever
cookie arrives is overwritten by the response. The residual concern is login
CSRF, and `SameSite=Strict` is what answers it.

### One middleware, not two

The CSRF check and the cookie-to-header bridge run in the same middleware
because the order between them matters, and two middlewares would leave that
order to registration. It is registered *inside* CORS: a browser refused at the
preflight never sends the real request, so a CSRF refusal outside CORS would
reach the page as an unexplained network error.

### What changed in the frontend

Two files, both pure transport: `api/client.js` and
`superadmin/platformClient.js`. No component, no stylesheet, no rendered
element. The three exported token functions stayed — a dozen screens import
them — but now answer whether a session exists rather than what it is, and they
also clear the old `localStorage` keys, since leaving one would strand a real
token in storage indefinitely.

## D-020 — A value in scope that nobody passed

Two defects of one shape, both found by sweeping the source rather than by
testing behaviour, and both fixed by threading a value that was already there.

**The welcome was always Messenger's.** `build_main_menu_response` takes a
`channel`, defaulting to messenger for the preview. Five call sites in
`core/engine.py` had `request.channel` in scope and passed none of them — so a
company that wrote a different greeting for WhatsApp, or turned the greeting off
for one channel, got the Messenger answer everywhere. The switch saved, the
screen showed it, and the customer never saw it.

Four were found by reading; the fifth was found by the test written for the
first four, which is the argument for the test existing at all.

**Pinning was recorded and hidden.** `set_pinned` writes `conversation_pinned`,
and the timeline's `meaningful_types` allow-list left it out — so the row was
written and then filtered away. Nobody could ever see it, and nothing said so.

The general rule, now asserted: an event worth writing is an event somebody has
to be able to read. A name that appears exactly once in that service is either
written and never shown, or listed and never written.

## D-021 — Telegram does not work, and fixing it is a product decision

`channels/telegram/bot.py` calls `message_gateway.handle_text` without
`company_id`, which has been a required argument since the platform became
multi-tenant. Every message raises `TypeError`. Verified directly rather than
inferred.

The module is not imported by `main.py`, has no tests, and cannot run, so
nothing depends on it today. It is left in place and recorded here rather than
quietly deleted or quietly repaired, because the fix is not mechanical: a
Telegram bot has one token per bot, so supporting it means a per-company account
type, credentials, routing and a place in `SUPPORTED_CHANNELS` — which is
currently `messenger`, `instagram`, `whatsapp`.

Whether this platform sells Telegram is not a question the code can answer.
Until it is answered, the interface offers Telegram in three places
(`ConversationsPage`, `NotificationsPage`, `UISettingsPage`) that the backend
does not support, and that mismatch is the visible half of the same decision.

## D-022 — The shared flow was still reaching every company's customers

The platform fixed this defect twice and missed the third copy. The shared
`bot_profile.json` that put one company's persona in everybody's prompt was
moved into each company's database (D-005). The shared branding and menu in the
assistant's reply were moved too. The **scripted flow** was not.

`features/*/flow.json` is T-ZONE's own IPTV support script — a language picker,
then a menu reading "📺 IPTV · 🛍️ Sales · 📞 Telecom Services · ℹ️ About
T-ZONE". `FlowLoader` read it once at import and served it to anyone who asked.

What made it reachable was a second shared file. `config/automation_policy.json`
shipped WhatsApp as `meta_agent_only` and Telegram as `flow_only`, so on those
two channels `should_auto_reply_with_ai` was False **for every company on the
platform** — the assistant never took priority and the engine fell through to
that flow. Running the engine for an arbitrary company id returned T-ZONE's menu
verbatim on both.

`handle_start` made it worse: a branch on `channel == "telegram"` pinned the
customer into `telegram_iptv_start` and forced their department to `iptv`. A
channel is not a business, and nothing about Telegram implies IPTV.

It survived because Messenger and Instagram shipped as `auto_reply` and never
reached the flow at all — so the leak only ever showed on the two channels
nobody was exercising.

### A flow is a company's script, not platform code

`FlowLoader.get_state` now takes the asking company and serves the shipped flows
only to a **single-company installation** — the same test
`channels/credentials.py` already uses before letting the environment's token
stand in for a connected account. As soon as a second company exists there is no
safe answer, so the answer is none. It fails **closed**: an unanswerable
ownership question serves no flow, because the other direction answers a
customer with somebody else's menu.

A company with no flow of its own falls through to the assistant, which reads
that company's own departments, knowledge and profile. That is already how
Messenger and Instagram work, so this is not new behaviour — it is the existing
behaviour, finally applied to the two channels that were missing it.

### Automation is a company's decision

`automation_policy` now resolves per company: the shipped file is the platform's
starting point, and a company overrides it in its own `ai_behavior` settings
under `channels`. The same relationship `config/response_policy.json` has to
`reply_policy_service`. An unrecognised `ai_mode` is ignored rather than stored,
because a typo would make `is_ai_enabled` false and leave that company answering
nobody on that channel.

Every read fails soft: an absent company, an absent section or an unreadable
database all fall back to the shipped values. Nothing here can stop a customer
being answered.

`core/menu.py` was deleted with this. It read `data/menus.json` — a third copy
of the same T-ZONE menu — and had no callers at all.
