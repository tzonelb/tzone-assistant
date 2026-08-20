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

## D-023 — Telegram becomes a channel a company connects

Telegram worked. `channels/telegram/bot.py` held `TELEGRAM_BOT_TOKEN` from the
environment, polled, and answered customers — for exactly one company, because
one process holds one token. Then the platform became multi-tenant,
`message_gateway.handle_text` gained a required `company_id`, and nothing
updated the one caller that lives outside the request path. Every message has
raised `TypeError` since, silently: no test covers a standalone script and
`main.py` never imports it.

The regression is not the interesting part. The shape is: that bot went through
the engine's Telegram branch, which pinned every conversation into
`telegram_iptv_start` and forced the department to `iptv` — T-ZONE's own IPTV
support script, applied to whichever company ran it. A channel is not a
business, and nothing about Telegram implies IPTV.

### One bot per company, routed by its own id

`telegram` is now a channel account like the others. The company pastes the
token BotFather gave it; the numeric prefix of that token *is* the bot id, so
`channel_account_service` derives the routing identifier rather than asking an
operator to type it — a mistyped id either receives nothing or claims an id
another company was routing on. It is stored in `external_account_id`, which
already carries a unique index per channel, so two companies cannot claim one
bot.

### The webhook, not the poller, is how a platform serves many

`POST /webhook/telegram/{bot_id}` mirrors the Meta and WhatsApp webhooks. The
bot id is in the path because Telegram delivers each bot's updates to whatever
URL that bot registered, so the URL is where the identity belongs.

Telegram has no request signature. What it has is a secret registered with
`setWebhook` and echoed in `X-Telegram-Bot-Api-Secret-Token`, stored per account
in the existing `verify_token_sealed` column and compared with
`compare_digest`. An account with **no** secret registered is refused, not waved
through: a bot id is public — it is in the bot's own username lookup — so an
unauthenticated endpoint would let anybody post into that company's inbox as
any customer they chose.

The polling script is kept and fixed, because it is genuinely the right tool for
local development and for a single-company install behind a firewall. It now
resolves its own company from its token at startup and refuses to run on a token
nobody has connected, rather than starting and answering as whichever company
the engine happened to resolve.

### The name arrives with the message

`resolve_meta_profile` answers only for Messenger, by design — there is no Graph
API to ask for anyone else. But Telegram sends the sender's name with every
update, and `inbound.py` read only the profile lookup, so a Telegram customer
would have appeared in the inbox as a numeric chat id while their name sat
unread in the same request.

## D-024 — A guard that was enforced and could never be armed

`super_admin_setting_overrides` has been read by `company_settings_service`
since the table shipped. It pins a value for one company, it can mark a key
locked, and `update_section` already refuses to write a locked key.

Nothing ever wrote a row. The read side worked, the enforcement worked, and the
feature was unreachable from either end — every company's `locked_keys` was `[]`
for ever, because there was no way to put anything in it.

That is the same defect as a switch that saves and decides nothing, arriving
from the opposite direction. Both leave an operator believing something is in
force that is not.

`value` and `is_locked` are independent, and deliberately so. An operator may
want to lock a company to whatever it has already chosen — a support agreement,
a compliance requirement — without deciding the value for them; and may want to
correct a value without taking the control away. Requiring both would make the
gentler action impossible.

Omitting `value` leaves any existing pin untouched, which is why the parameter
defaults to a sentinel rather than to `None`: `None` is a legitimate thing to
pin. A setting key no section defines is refused rather than stored — it would
otherwise sit in the table for ever, pinning nothing and locking nothing, while
the console showed it as applied.

The audit records the key and whether it is locked, never the value: a settings
value can hold a workspace code, and `audit_log` is shared across companies.

## D-025 — A branch id that was not yours

Every table in both schemas was checked for readers and writers. `branches` had
four readers and no writer anywhere, which is what drew attention to it. The
readers turned out to be the more serious half.

`channel_accounts.branch_id` and `company_users.branch_id` were written straight
from the request payload, and ids in the control database are global — another
company's branch id is a valid row. Three read joins matched on the id alone
with no company condition, so the other company's branch *name* came back on the
Channels screen, the dashboard and the team list. Proved before fixing: with two
companies provisioned, Alpha's channel list returned
`branch_name = 'Beta Secret Warehouse'`.

One name per row, not a bulk dump. It is treated as a leak anyway, because the
size of one is not what makes it one.

What let it survive is the part worth remembering. At both write sites the
neighbouring pointer *was* checked, each with a comment explaining why an id
from another company must be refused — `department_id` in
`channel_account_service`, `role_id` in `roles.py`. `branch_id` sat in the same
argument list and the same plain-column tuple and was not. The reasoning had
already been done and applied to the field beside it.

Closed on both sides: refused at the write, because a stored pointer to someone
else's row is wrong even while nothing displays it, and the joins scoped by
company so a row written before the change displays nothing either.

Recorded and deliberately not fixed: nothing on the platform can create a
branch. Two screens offer the field and both lists are permanently empty.
Closing that needs a screen for managing branches, and the design is frozen by
instruction, so a test states the gap and fails the day a writer appears.

## D-026 — The page the company chose, and the page the post went to

`scheduled_posts.channel_account_id` shipped with the scheduler. The create
endpoint accepted it, the row stored it, and nothing read it back — the
publisher called `resolve(company_id, channel)`, which returns the company's
lowest-numbered active account on that channel.

For a company with one page that is the same page, which is why nobody noticed.
For a company with two, the post went to the wrong audience. It is a switch that
saves and decides nothing with a worse ending: the result is public, on the
company's own followers, at a time nobody is watching.

The ownership check lands in the same change as the feature, not after it. While
nothing read the column an unvalidated value was inert; honouring it is exactly
what would turn an id from another company into an instruction to publish
through that company's page with that company's token.

When an account is named and does not resolve, sending raises rather than
falling back. The caller asked for a specific page, and quietly publishing to a
different one is the defect being fixed, not an acceptable degradation.

## D-027 — Not connected, and not connectable

Two screens kept their own copy of the channel list and both ended in `website`.
It is not in `SUPPORTED_CHANNELS`, has no routing field, no webhook and no
sender, and cannot be chosen on the Channels screen. Every company saw a Website
tab on its inbox, captioned "Website is not connected yet", and a Website option
in its notification filter.

The word was not a typo. It was a copy of the catalogue that nobody updated when
the real one changed, so deleting it would leave the next copy to drift the same
way. The inbox now reads `supported_channels` off the response, and the single
remaining constant is only what a screen shows before the first response
arrives. A test fails on any screen that names the channels in a row rather than
importing them.

## D-028 — Twenty-three events that were named and never raised

`Action` names 43 things worth recording, and the company owner reads them
through one unified log built for exactly that. Twenty of the names were ever
written by anything.

Nothing here is a crash or a wrong answer, which is why it lasted. It is worse
in one specific way: the owner opens a log built to tell them what happened in
their company, reads it to the end, and concludes nothing else did.

Two of the missing events matter more than the rest.

**A rejected workspace code.** `authenticate` checks the code last — after the
email matches, the account is active, the company resolves, and the password
verifies. Reaching that branch means somebody holds a working password for one
of this company's employees and is stopped by the workspace code alone: either
an employee who forgot one of their four credentials, or a compromised password
one secret away from an open door. Only the owner can tell those apart. It went
to a log file on the server and nowhere else.

The uniform 401 is unchanged, and tested. Withholding the reason from an
attacker and withholding it from the owner were never the same decision, and
only the first was ever intended.

**A refused webhook.** Forging a delivery is the one attack on this platform
that needs no account at all. The signature check was correct and fail-closed
from the start; there was no record an operator could read.

Two properties are enforced rather than assumed. Entries carry field names, ids,
statuses and timestamps — never message bodies, tokens or contact details; a
settings section in particular is an open bag that can hold credentials. And the
two events whose rate an attacker controls, a refused webhook and a refused
action, are throttled per source and per employee-and-permission, so the audit
trail cannot be made into the payload.

## D-029 — A permission that restricted nothing

`subscriptions.manage` was seeded into every company's control database,
described as "Change the plan and billing details", listed on the Roles screen
beside the ones that work, and checked by no endpoint. Nor could it be: a
company cannot change its own plan by design — plans and per-company overrides
are set from the operator console.

`require_permission` already said why this matters, in its own docstring: a role
screen that lists permissions the API never checks is worse than no role screen
at all, because it tells an administrator they have restricted somebody when
they have not. A permission is a claim about what someone is prevented from
doing, and an owner who believes a restriction is in place stops looking.

Retiring it from the catalogue is only half. The seed upserts on every boot, so
a dropped permission would keep its row for ever in every database provisioned
before the change — which is every database in production. The seed now deletes
permissions that are no longer in the catalogue, the same way it already treats
the catalogue as the authority for a permission's name and description.
`role_permissions` cascades, so a role still granting a retired permission loses
the grant. That changes no access: nothing was checking it.

## D-030 — Two settings catalogues, one seeded and one served

The audit of D-013's shape reported eight settings "stored and read by
nothing". That was true and it was not the real defect. The real one is that
there were **two** `DEFAULT_SETTINGS`: one in `database/schema_tenant.py`,
seeded into every company's database at provisioning, and a different one in
`backend/services/company_settings_service.py`, which is what `get_section`
returned and what `update_section` accepted.

They had drifted completely apart. The seeded catalogue held `working_hours`,
`reply_language`, `escalate_on_low_confidence` and three `notify_on_*` keys.
The served one held none of them, and merged its own set over the top.

So those keys were not merely unread. There was no path to them at all: a
company's database held them, a read never returned them, and a write naming
one was silently dropped. Nothing failed, because dropping a key nobody defines
is the correct behaviour for a key nobody defines — which is exactly what made
the fork invisible.

The check that was supposed to catch this class parsed the constant out of
`schema_tenant.py` with `ast`. It was auditing the dead one, and passing.

`schema_tenant.py` now owns the single catalogue and the service imports it, so
seeding and serving cannot disagree without one of them failing to import.
`RESOLVED_SECTIONS` names the two sections that must not be seeded at all —
`company_profile` comes from the control-plane row and `modules` from the
operator's switches, and a stored row wins over a resolved default, so seeding
`company_profile` wrote `company_name: ""` into every new company and the
settings screen opened showing an empty name.

## D-031 — Six settings built, two retired

With one catalogue, the settings that decide nothing could finally be resolved.

Built:

* **`working_hours`** decides escalation, not the assistant. A bot that stops
  answering outside office hours is worse for the customer than one that keeps
  going; what is wrong is telling somebody "our team can check it for you" at
  three in the morning, which promises a person who is not coming until the
  shop opens. The conversation is still handed over. The sentence after it says
  when.

  Every failure reads as open — an unknown timezone, a malformed time, a day
  that is not a mapping. A company whose hours are corrupt gets exactly the
  behaviour it had before hours existed. The opposite default would let a typo
  tell that company's customers it was closed while the team sat there.

  The first implementation collapsed "the company marked this day closed" and
  "this day cannot be read" into one answer, so a typo in one day's opening
  time closed the company on that day for ever. Found by its own tests.

* **`reply_language`** replaces detection, not the customer's choice. A
  customer who explicitly asks to switch is handled earlier and still wins;
  overriding them would make that feature lie about what it did.

* **The three notification preferences.** Two of them had to have their
  notifications built first: a colleague taking a conversation off the
  assistant was recorded only as a conversation event, and the assistant
  failing to answer a customer left a `diagnostic_events` row nobody watches
  and that clears after fourteen days. The team's first hint that the assistant
  was failing was a customer asking why they had been ignored.

  The gate lives inside `notification_service.create`, keyed by notification
  type, rather than at each call site — which is how these came to be offered
  without existing in the first place.

Retired, because the decision already had an owner elsewhere:
`escalate_on_low_confidence` (duplicated `fallback_to_human` in the reply
policy), `welcome_immediate` (duplicated `welcome_enabled`/`welcome_mode`),
`reply_only_when_customer_stops_typing` (duplicated
`collect_message_delay_seconds`), and five browser preferences the browser
already keeps per user. Two switches for one decision leaves an owner setting
the one they found and unable to tell why nothing changed.

Retirement is named in `RETIRED_SETTINGS`, not inferred from absence. The first
version dropped every stored key the defaults did not mention, which sounded
equivalent and was not: `ai_behavior.channels` is sparse per-channel
configuration that deliberately has no default, and deleting it silently turned
off per-channel AI mode for every company.

## D-032 — Branches a company can create

The other half of D-025. `branches` was read in four places and could not be
written, so the branch selector on every team member and the branch field on
every channel were permanently empty lists.

Endpoints under `users.manage`, and CLI commands for the cases with no session
— a company being set up before anybody has a password, an operator moving a
customer's locations across in bulk.

Deleting a branch relies on the foreign keys, which both pointing tables
declare as `ON DELETE SET NULL`, rather than repeating the rule in SQL. Two
mechanisms for one rule means the redundant one rots. That leaves the pragma as
the thing worth testing: with `foreign_keys` off this silently stops working
and a deleted branch's id lives on, ready to be handed to a different branch
later.

Getting there took three wrong statements about the schema, each from a `grep`
whose context window cut off before the `FOREIGN KEY` lines. The tests were
what caught all three.

## D-033 — `website`, in the five places the first fix missed

D-027 recorded `website` as removed. It was removed from two of five places.

It survived in `UISettingsPage.jsx` as a visible **"Website" toggle** on every
company's Preferences screen, in the per-user notification defaults, and as an
icon entry. The check written to prevent exactly this looked for a quoted list
and never saw an object written as bare keys.

Rewriting the check went through two more wrong versions. The second flagged
any map keyed by channel, which condemned the label maps in `ChannelsPage` and
`AiTeachingPage` — mapping a code to a display name is the right thing to do
there, and an unknown code costs nothing worse than an unprettified label.

The property that actually matters is narrower: a file may name the channels,
and may not offer one that does not exist. The check also skips comment lines,
after a comment in this repository explaining that `website` used to be offered
failed the check that `website` is not offered — the fourth time in this audit
that a source-scanning check matched somebody's prose about a defect instead of
the defect.

## D-034 — The before-and-after finally has a reader

`company_setting_audit` and `customer_audit` have held the old and new values
of every settings change and every customer edit since each shipped. No
endpoint read either. The table sweep found them, and the first answer was to
declare them in a registry with the reason they were kept.

Declaring a gap is not closing it. Rows kept accumulating where nobody could
open them, and nothing pruned them — an unbounded store of one company's old
phone numbers with no reader and no retention is not a record, it is a
liability that happens to be encrypted.

Two readers, each behind the permission that already guards the thing it
describes rather than behind `settings.view` like the rest of the log. The
unified log names *which keys* changed and never the values, because a settings
section is an open bag and a customer field is somebody's phone number. The
values are the sensitive half: reading what a setting used to be sits closer to
being able to change it than to being able to see it, so the settings history
asks for `settings.manage`; reading what a customer's number used to be belongs
to whoever may see the number now, so the customer history asks for
`customers.view`.

Retention on the same clock as the entry the detail belongs to. Longer would
leave values in the database after the record of who changed them is gone;
shorter would leave a log entry pointing at a detail that no longer exists.

A row that will not parse degrades to `None` rather than raising. The history
is most needed when something is wrong, which is exactly when a row is most
likely to be malformed.

Every registry this audit created is now empty: no setting is unimplemented, no
permission is unenforced, no action is unraised, no table is write-only, no
shipped file is orphaned, and no module is ungated.

## D-035 — An audit write that waited fifteen seconds on its own caller

Found by pressure, not by reading, and it had been in the source the whole
time: refusing one channel connection over a plan limit took **15.06 seconds**
and then lost the security record it was refusing about. No concurrency, one
request, an idle machine.

`create_account` opens the control database with `BEGIN IMMEDIATE` and checks
the plan limit inside that transaction — correctly, because the check and the
insert have to be atomic or two requests racing both walk past a limit of one.
The refusal is mirrored to the control plane as a security event, and the
mirror opened a **second** connection to the control database and tried to
write. That connection waited for a lock held by its own caller on its own
thread. SQLite cannot detect that and does not try; it blocks until
`busy_timeout` expires and then fails. `_mirror` catches its own failure — by
design, so that recording a refusal can never change the refusal — so the whole
thing was silent.

Under load it was worse than slow. The stalled transaction holds the control
database's write lock for the full fifteen seconds, and the control database is
where sessions, users and channel accounts live. One company hitting its plan
limit stalled writes for every company on the platform.

Two things had hidden it. The existing test asserts the refusal *is recorded*
and passes, because the record it checks is the one in the company's own
database — a different file, no contention. And a fifteen-second success looks
exactly like a slow test.

**The mechanism, not the instance.** `DatabaseManager.after_release` runs
queued work when the thread has closed every database. `activity_service`
routes both its writes through it. Queued rather than joined to the caller's
transaction on purpose: sharing the connection would have removed the stall
too, and would have thrown the record away every time, because the transaction
that records a refusal is precisely the one that gets rolled back.

A probe on `control()` was used to enumerate the rest of the platform rather
than reason about it. Of the four places a plan limit is checked, two hold a
write lock at the moment they check (`create_account` and `update_account`,
both `BEGIN IMMEDIATE`) and two do not — the checks in `roles.py` and
`knowledge_service.py` run before any write in their block, so no lock is held
and no deadlock was possible. That was measured, not assumed.

Limit worth stating: deferred work is lost if the process dies between the
transaction closing and the queue draining. It was already best-effort — the
old code caught and swallowed its own failures — so this trades nothing, but it
is not durable and should not be relied on as if it were.

## D-036 — The settings section from the URL was never checked

`_normalize_section` was already written, already correct, and already wired
into `set_override` and `clear_override` — the two Super Admin methods. The
company's own `get_section` and `update_section`, in the same class a few lines
away, used a bare `.strip().lower()`.

So `PUT /api/settings/anything` stored a row under whatever name was in the
path. Not a leak and not a traversal — it is a column value, not a file — but
nothing ever read it back, so the only visible effect was one company's
settings table and its settings-history log growing by a row and up to a couple
of hundred kilobytes per request, from a door that needs only `settings.manage`.

This is the third finding in this audit with the same shape: the reasoning was
done, written down, and applied to the neighbouring thing. `branch_id` sat in
the same argument list as a `department_id` that *was* checked. `website` was
removed from two of the five places that offered it. A check that exists and is
not called reads, in review, exactly like a check that is called.

## D-037 — What the pressure found, and what it did not

Six areas were put under load. Two produced defects, above. The other four were
clean, and the mutations that prove the tests could have failed are recorded
here so that "clean" means something.

* **Duplicate replies.** Twelve workers claiming one due batch simultaneously:
  exactly one claim, and a later sweep finds nothing while the lease holds.
* **Conversation ownership.** Eight employees taking over at once: exactly one
  owner, the rest refused rather than crashed.
* **Plan limits under concurrency.** Ten simultaneous connections against a
  limit of three: three created, seven refused, none by database error. The
  test detects both failure modes — weakening the transaction from `IMMEDIATE`
  to `DEFERRED` keeps the count correct and turns the polite refusal into a
  500, and an earlier version of the test, which asserted only the count,
  stayed green through exactly that.
* **Login lockout.** Twenty guesses released on a barrier: the account locks,
  every failure is counted exactly once, one under the threshold does not lock,
  and an unlock survives the burst that caused it. Three separate mutations of
  the lockout code each fail it.

Two limits stated rather than glossed. The walk-every-page test **cannot**
detect a missing `ORDER BY` tiebreaker — dropping `, id DESC` leaves it green,
because SQLite happens to fall back to rowid order for that plan. That is
incidental and can change with an index or a version, so the tiebreaker is
asserted separately against the query text. And the concurrency harness itself
had to be fixed before any of its results meant anything: services bind
`database_manager` by value at import, so a service first imported during an
earlier test keeps that test's database for the life of the process, and
rebinding by identity silently stopped working after the first test in a
session. A race test quietly reading the wrong database reports "no race" no
matter what the code does.

## D-038 — The same missing check, a fourth and fifth time

Every module was audited on its own, and then the platform was attacked from
the position an attacker really has: a valid account.

**141 routes, all guarded.** Establishing that took three attempts, and the two
failures are worth recording because both looked like findings. The first
scanner read only a literal `require_permission("...")` inside the route
function and reported *all 141* as unguarded — most guards are named dependency
aliases (`view_context`) defined once per file. The second followed the aliases
and reported 22, of which 21 were wrong: `team_chat` passes a module constant,
`require_permission(PERMISSION)`, and `roles.overview` guards inside its body.
The six that remain carry no permission on purpose — a person's own
notifications, where there is nothing to be permitted to see.

**Where a cross-tenant read is even possible.** Mutation settled this rather
than reasoning. Deleting `AND company_id = ?` from a *tenant* table's lookup —
products, knowledge, tasks, customers — changes nothing, because Alpha's
connection cannot open Beta's file. The `company_id` column there is defence in
depth. Deleting it from a *control* table's lookup — channel accounts, branches
— fails immediately, because an id from another company is a real row on the
same connection. So the control plane is the whole surface, and it is where
this platform has already had a leak.

**Three payload fields accepted another company's id**, all found by trying it:

* `appointments.staff_user_id` — an appointment booked against an employee of
  a different company. The name never surfaced (`user_display_names` is
  scoped), but the row pointed at a stranger who then held a slot in a calendar
  they do not work in, and the double-booking guarantee stopped meaning
  anything for that slot.
* `appointments.branch_id` — stored raw.
* `team_chat.create_channel(member_user_ids=…)` — a channel created with an
  employee of another company in it.

The third is the one that says something. `add_member` has this check, and has
had it from the start, with a comment reading "the invitee must be an employee
of this company. Without this check a caller could name any user id in the
platform." `create_channel` takes the same list of ids and did not.

That is the fourth and fifth time in this audit: `channel_accounts.branch_id`
beside a checked `department_id`; `company_users.branch_id`; `website` removed
from two of five places; `_normalize_section` wired into the Super Admin pair
and not the company pair; now this. Five instances, one cause — **a check that
lives inside the function that happens to need it is invisible to the next
function that needs it, and reads in review exactly like a check that is
called.**

So the answer is not a sixth inline check. `backend/services/ownership.py` is
the one place that answers "does this company own that id?", every caller gets
the same refusal, and `tests/test_foreign_ids_in_payloads.py` is the registry
of every payload field that carries a control-plane id. Deliberately no
validator for tenant ids: `category_id`, `customer_id` and `conversation_id`
point into the company's own file, and a check there would imply a risk that
does not exist.

One behavioural regression was introduced and caught by the existing suite
before it shipped. Validating the staff id on *reschedule* meant an employee
leaving froze every appointment already in their calendar — nobody could move
or cancel them, which is exactly when a company needs to. Only a staff id the
caller **supplies** is checked now; carrying the existing one forward is not a
claim about it.

## D-039 — A model call nobody counted

`plan_service.record_usage` had exactly one caller in the repository: the live
reply path. `POST /api/ai-teaching/dry-run` runs the real assistant — its own
docstring says "the model call itself is not suppressed, the point is to see
the real reply" — and recorded nothing.

Two consequences. Every usage screen and every invoice under-reported what a
company actually spends, because a whole category of model call was missing.
And anyone holding `settings.manage` could script the endpoint: nothing counted
it, nothing capped it, no number on any screen moved, and the first evidence
would be the invoice.

Found by asking who calls the counter, not by reading the endpoint. The
endpoint looks careful — it is guarded and documented at length, and every line
of that documentation is about what a preview must *not* touch: no message
stored, no reply queued, no conversation state changed. What it *spends* was
not on the list.

Counted now under its own metric. Folding previews into `ai_replies` would make
every usage screen count a company's own testing as conversations it had.

Capped as a hard platform limit rather than a plan allowance, and the
distinction is deliberate: a plan limit is the operator's commercial decision
about one customer, while this is the platform declining to let any single
account spend without bound — not something a larger plan should be able to
buy. Two thousand a month, two orders of magnitude above real use, and settable
to zero for an operator who would rather not have it. It fails **open** on a
counter it cannot read: some untracked calls during an outage is a smaller harm
than every company's tuning screen going away because one query failed.

## D-040 — What the attack found, and what it did not

Twenty-three attacks, each performed against the real API rather than reasoned
about. All refused. Recorded here so that "secure" means something specific:

* **Privilege escalation** — an agent cannot set their own role, rewrite their
  own role's permissions, create an account, reach settings they hold no
  permission for, unlock an account, or force a colleague's password reset.
* **Scope crossing** — a company owner, the most privileged person in their
  company, is nobody at all in the platform console.
* **Tokens** — a tampered token, an absent one, `Bearer null`, and a token
  after sign-out are all refused. Sign-out revokes rather than forgets.
* **Injection** — five payloads through the customer search box; the table is
  still there afterwards, which is the assertion.
* **Secrets** — sixteen screens, walked as JSON keys rather than grepped as
  text, plus planted sentinel values. Nothing leaks.
* **Enumeration** — a wrong password and an unknown address answer identically,
  in status and in body.
* **Mass assignment** — `company_id` and `id` in a payload change neither.

Also asked, and separate: inside **one** company, can one employee act as
another? Two colleagues on the same role hold an identical permission set by
design, so the permission model cannot answer it — the separation is whether
each endpoint scopes to the caller's own id. Notifications addressed to a
colleague are not listed, not markable, and not cleared by "mark all read";
private channels are invisible to non-members; a colleague's message cannot be
edited. Each verified by mutation, and one of those mutations initially hit the
wrong line and had to be redone — a mutation that does not land where intended
proves as little as the test it was meant to check.

**Performance, measured rather than assumed.** Six list screens were loaded at
five rows and at forty, counting SQL statements from SQLite's own trace. Every
one is constant: no screen issues a query per row. Reported alongside, because
it is the number that will matter next: a request opens between five and eleven
encrypted databases, and each open costs about 1.0 ms for the control plane and
1.4 ms for a company file — so roughly fifteen milliseconds of every inbox
request is connection setup. Not a defect at this size, and worth having
written down before somebody adds the twelfth.

## D-041 — A session that outlives the access, and a retention nothing ran

Two more rounds of attacking the platform from inside a working account.

**Sessions end when access does.** A token is minted at sign-in and lives for
hours; whether the holder is still allowed to be here is decided once, at that
moment. `get_user_from_token` re-checks the platform account (`users.status`)
and not the membership (`company_users.status`), so the obvious worry was the
Tuesday-morning case: somebody is let go, their manager switches their
membership off, and the browser they left open keeps working until the token
expires.

It does not. All four ways access can be taken away — disabling the membership,
deleting it, disabling the account, suspending the whole company — close every
door immediately, including the routes that carry no permission at all, because
`resolve_company_id` consults `get_user_companies`, which filters on membership
*and* company status. Verified by mutation on both filters separately, each
failing exactly the test that names it.

Worth stating because it is not obvious from the token check alone, and the
next person to read `get_user_from_token` will have the same worry.

**A retention window nothing ran.** `DiagnosticsService.RETENTION_DAYS = 14`
has been in the source since the service was written, and the only thing that
applied it was a button in the developer console. For every company nobody
pressed it for, the number was a comment.

That table fills faster than any other on the platform, and from ordinary use
rather than any attack: nine `diagnostics_service.record` calls sit on the path
of one inbound message — seven in `smart_reply`, two in `inbound` — so a
company handling a thousand messages a month writes tens of thousands of rows a
month into its own encrypted database, for ever.

The periodic sweep that already opens every company to prune the activity log
now applies it too, each company guarded separately so one unreadable database
does not cost the rest their retention. No new policy: fourteen days was
already decided and written down, and this only makes it true.

**A hazard this audit documented, then walked into.** The first version of the
test imported `main` *inside* the test body, while the fixture's patch was
active. Every module `main` pulls in for the first time copies whatever
`database_manager` currently is, and monkeypatch cannot restore a module that
did not exist when the patch was recorded — so those modules held a temporary
directory for the rest of the process. 84 tests in two unrelated files failed,
none of them near the change.

This is exactly the trap written up under D-037 for the concurrency harness,
found again three files later by someone who had just written the warning. The
fixture now imports `main` before the sweep and asserts it was rebound, which
is the pattern the rest of the suite already uses — and the reason it is worth
a paragraph is that knowing about a trap is demonstrably not the same as not
falling into it.

## D-042 — A lapsed subscription stops the company

The owner's decision, taken explicitly after being asked. Until now expiry did
nothing: `plan_service.is_active` was computed, shown on a screen, and
consulted by no code path that could refuse anything. A company could stop
paying and carry on indefinitely, and the operator's only lever was suspension
— a much heavier act that reads to a customer as an accusation rather than an
invoice.

**One gate, not seventeen checks.** `subscription_gate` answers one question —
may this company operate today — and the HTTP layer and the assistant both ask
it. Written this way because the alternative has failed five times in this
audit already: a check repeated at each call site is a check that gets missed
at one of them, and the missed one is a door nobody knows is open. The module
gate has the same shape for the same reason, and both now sit in the same
helper in `main.py` so a router registered later cannot pick up one and miss
the other.

Four parts, each a separate decision:

* **`402 Payment Required`, not `403`.** A 403 tells somebody they are not
  allowed. This company *is* allowed and has not paid, and the employee reading
  the message is usually not the person who pays.
* **The assistant stops answering.** This is what makes it real. Screens nobody
  can open is an inconvenience; an assistant still replying is the service
  still being delivered, free, with no reason to renew.
* **Customers' messages are still stored, and still notify.** A customer owes
  nobody anything, and a company that renews on Thursday must find Tuesday's
  messages waiting rather than a hole.
* **Nothing is said to the customer.** Silence, not "this business has not
  paid" — which would expose the owner to their own customers, a worse thing to
  do to them than the pause itself.

**One router stays open**: the dashboard, which carries
`/api/dashboard/subscription`. Pausing the screen that explains the pause makes
the pause unactionable, and prompting an action is the whole point. That
exemption is pinned to exactly `["dashboard"]` by a test reading `main.py`, so
a second one cannot be added quietly.

The grace period is the operator's and already existed: `is_active` treats
`grace_period_until` as entitlement, so setting it per company is how an
operator says "the payment is late and the service continues". Nothing here
invents a second one. Renewal invalidates the cache immediately rather than
after thirty seconds, because that half minute is exactly when an operator is
watching, having just told a customer they are back on.

Fails **open** on an unreadable control plane, like every other gate here: a
blip in one database must not take every company off the air over a bill none
of them owe.

## D-043 — `website_chat`: the same defect, one word away from the guard

D-033 removed `website` from the five places that offered it, and wrote
`tests/test_channel_catalogue.py` so it could not come back. Every check in
that file passed while **`website_chat` sat in six lists**, including two that
decide real behaviour.

The guard missed it twice over, and both misses are worth naming:

* its watch-list held `"website"`, and the spelling here was different by one
  word — a watch-list is only as good as somebody's memory of what to watch for
* it compared the *frontend* list to `channel_account_service.SUPPORTED_CHANNELS`
  and never looked at any other backend list

And there was another backend list. `config.SUPPORTED_CHANNELS` held five
channels where the real catalogue holds four. Nothing read it — every reader
imports the catalogue — but `reply_policy_service` carried a comment citing it
as the source its own list mirrored. A dead constant that documentation points
at is worse than a dead constant, because it is believed.

The consequence was a screen offering something the platform cannot do. A
company could bind a persona to "Website chat" and write a whole reply policy
for it: no routing field, no webhook, no sender, so no message would ever
arrive on it and no policy would ever be read. That is the same class of defect
D-031 closed for settings — a decision that saves and decides nothing — moved
into the channel picker.

Fixed by deriving rather than repeating: `PREVIEW_CHANNELS` and
`POLICY_CHANNELS` are now `tuple(SUPPORTED_CHANNELS)`, the dead config constant
is gone, and the API's `Literal` is pinned to the catalogue by a test.
`IntentTransitionManager.GENERAL_CHANNELS` had the opposite problem in the same
list: `website_chat`, which cannot deliver, was in it, and Telegram, which can,
was not.

The new check imports the constants and compares them, instead of recognising
names in source. A list added anywhere with an invented channel in it now fails
on the day it is written, whatever it is called.

## D-044 — `main.py` split, and every module driven end to end

`main.py` had grown to 582 lines holding three unrelated things: what the
application *is* (routers, middleware, gates), what it does *at boot* (the
lifespan), and what it does *forever after* (five background workers). Reading
any one meant scrolling past the other two, and the workers are the part most
likely to be edited by somebody with no business near the router registration
two hundred lines below.

The workers moved to `backend/workers.py`, which imports no FastAPI at all —
so a worker is now a coroutine you can call in a test without a request.
`main.py` is 310 lines and lists, in one import, exactly what the process runs
on a timer. Eleven imports were left unused by the split and are gone.

Separately, and overdue: **`tests/test_every_module_actually_works.py`** drives
each module through its own API and asserts on the *content* of the answer, not
the status code. Every other file in this suite asks whether something is
guarded, scoped, fast or refused — none asked the plainest question an owner
would ask, which is whether the screen does its job. A module can be perfectly
guarded and completely broken, and no permission test would notice, because a
permission test is satisfied by a 403 and a 200 and never looks inside the 200.

All fifteen pass, and one of them is how `website_chat` was found: the AI
Teaching response was printed in a failure and the channel list was in it.

## D-045 — The gate had three doors, and I had shut two

Attacking my own change from the previous entry, rather than the platform.

`require_active_subscription` closes the API and `channels/inbound` declines to
queue a reply. Both are the doors somebody *asks* through. The third produces
customer-visible output with nobody asking: the background workers.

* `publish_due_posts` — a post approved last month still went out next
  Thursday. This is the consequence that is **public**: a paused company
  posting to its own followers is the platform delivering the service it just
  stopped charging for, in front of an audience.
* `process_due_replies` — batches queued in the minutes before the lapse were
  still due, so the assistant kept answering customers after the workspace was
  paused, quietly undoing the decision for as long as the queue lasted.

`publish_due_posts` had already made this exact argument. Its comment reads:
"a post going out to a company's followers from a module its team can no longer
open, and cannot cancel from inside the platform, is not a switch that was
ignored — it is the company posting to its own audience without an operator."
Every word is true of a paused subscription. The reasoning was written down and
applied to the gate next door. **Sixth instance**, and the first one where the
neighbouring gate was mine, added an hour earlier.

Both refuse without *claiming*, so the queue is intact and goes out the moment
the company renews. Refusing by discarding would punish the company for the
pause twice.

**And a worse one, entirely of my own making.** `is_active(None)` is False, so
reading it directly meant a company with *no subscription row* counted as
lapsed — and `create_company` takes `plan_code` as an optional argument while
the CLI has no `--plan` flag at all. Every company created today would have
been dark from the moment it existed: every screen 402, the assistant silent,
nothing in the console explaining why. Provisioning a company would have
produced a company that does not work.

A subscription that *ends* stops the company. Something that never began has
not ended. An operator who wants a company stopped before it is billed has
suspension, which is immediate and says so. Verified by creating a real company
through the real CLI and asking the gate about it.

Two things about how this was found are worth keeping. The stub in the first
version of the publish test returned `{"success": True}` where
`publish_due_posts` reads `ok` — so a *failed* publish looked like a refused
one, and the gate assertion passed whether or not the gate existed. It was the
**positive control** — "and renewing publishes it" — that failed and exposed
it. And the new-company case surfaced only because the publish test was run
against a fixture company that had no subscription, which is the state every
real company starts in and no test had exercised.

## D-046 — Two properties that would have been lost silently

Attacking the authentication and encryption primitives directly, rather than
the routes above them. Both properties below already held; both are now pinned,
because both could be removed by somebody tidying up and neither would fail a
single existing test.

**The sign-in form is not a user directory.** Measured, not assumed: an unknown
address answers in 188.6 ms and a wrong password in 182.9 ms — a ratio of
**1.031**, indistinguishable from noise. `_dummy_password_check` is what makes
that true, and it is four lines that look pointless, sit on a failure path
where nothing visibly depends on them, and would survive any review by somebody
removing dead work. Turning it into a no-op drops the ratio to **0.006** — an
unknown address answers in 1 ms against 206 ms, a 170-fold difference, and the
form becomes a way to ask which of ten thousand addresses have accounts here.
For a platform whose customers are businesses, that is a competitor's prospect
list, extracted with no credentials at all.

A second test guards the subtler version: `_dummy_password_check` still being
called but weakened. A refused sign-in that returns in under 20 ms has not run
310,000 PBKDF2 iterations, whatever the ratio says.

The leak that is **deliberately left** is pinned too. Branches after a correct
password are not equalised — unsealing runs 600,000 KDF iterations against the
password's 310,000, so a wrong workspace code is about three times slower than
a wrong password. That tells somebody who already holds valid credentials that
the company or the code stopped them. Equalising it would mean running the 600k
unseal on every rejected attempt, handing every anonymous caller a
CPU-exhaustion lever. The leak is bounded to someone who already has a working
password; the amplification would be available to everyone. The test asserts
the slower branch *stays* slower, so if it ever changes, somebody has either
equalised it or stopped proving the workspace code — both worth a conversation.

**A stolen sealed credential is worthless.** Channel accounts live in the
shared control database because a webhook must be routed before the company is
known, so one company's page token sits in the same table as everybody else's.
Three attacks, all refused: a dump with no key, a company copying another
company's sealed blob into its own row, and a blob moved between fields on one
row.

The instructive part is **which test caught which mutation**. Removing the
company binding from the sealed value left the cross-company attack still
failing — because the two companies already have different keys, so a key-only
design looks identical from outside. Only the "same key, wrong company id" test
saw it. A suite that had tested the obvious attack alone would have reported
the binding intact while it was gone, and the next place that looks a key up by
company id would have been one wrong variable away from opening the wrong
company's credentials. The per-field context needed its own mutation for the
same reason.

Also measured while here: the workspace code is 12 characters from a
31-character alphabet with the confusable ones (I, L, O, 0, 1) removed — 59.5
bits, a keyspace of 7.9 × 10¹⁷. At the login rate limit that is not a target.
