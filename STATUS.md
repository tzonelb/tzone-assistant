# T-ZONE Platform — Live Status

> **This file is the single source of truth for "where are we / what's left".**
> It is updated and pushed with every batch of work so any machine or session
> can `git pull` and immediately know the current state. If you are resuming
> from a different machine: `git pull origin claude/tzone-release-timeout-fixes-pesy2r`
> then read this file top to bottom.

**Last updated:** 2026-08-04
**Active branch:** `claude/tzone-release-timeout-fixes-pesy2r`
**Run command:** `uvicorn main:app` (root `main.py` is canonical — `backend/main.py` is legacy/dead, see docs/DECISION_LOG.md D-001)
**Target launch:** Thursday 2026-08-06
**Working practice:** work batches now run strictly one build agent + one verify agent at a time (never more than 2 concurrent agents), per user instruction.

---

## How to run / verify locally

```bash
# Backend
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8000
python3 -m pytest tests/ -q          # full backend test suite

# Frontend
cd frontend
npm install
npm run dev
npm run lint && npm run build        # must both pass
npx vitest run                       # frontend tests
```

Before production: set `FACEBOOK_APP_SECRET` and `TOKEN_ENCRYPTION_KEY` in `.env`
(webhooks reject all traffic without the app secret when `DEBUG=false`, by design).

---

## ✅ DONE — merged & pushed & verified

### Original reported bugs (all fixed)
- Company Settings tabs shown/editable without permission → real per-section permission gating.
- Dead "Configure" button → real generic per-section settings editor.
- Channels description said 6 channels, only 4 real → corrected + honest copy.
- Notification filter silently cleared on background refresh → race fixed.
- Customer/conversation detail silent overwrite → optimistic-concurrency (control_version CAS) + 409 handling.
- Reply "reply_mode" setting saved but never read by engine → engine now honors it.

### Missing features built from scratch
- **Broadcast** — compose, accurate recipient count, resume-safe send, history + live progress.
- **Facebook OAuth connect** — real connect/callback flow, encrypted per-company tokens, inbound company-routing.
- **Reply Flow Builder** — step editor with a labeled Escalation condition, wired into the engine.
- **Theme Studio** — working "Heading scale" control.
- **Analytics** — real BI page (KPIs, channel/status/department/AI-vs-human breakdowns, employee activity) over real DB data, `dashboard.view`-gated.
- **Customers** — real list/search/detail/edit UI wired to the (now RBAC + optimistic-concurrency-protected) customers API.
- **AI Teaching** — real knowledge/FAQ management UI (bilingual ar/en) wired to the company-scoped knowledge API.
- **Tasks** — company-scoped task/follow-up management (backend `tasks` table + `/api/tasks` CRUD + optimistic concurrency, `tasks.view`/`tasks.manage` RBAC) with a real filterable/paginated UI, assignee picker, mark-done, delete. Built as a clean retry from current HEAD after the first attempt's stale-base crash; 13 new tests (207/207 suite total), lint/build clean.

### Security hardening (2 full audit sweeps' worth of findings, all fixed)
- Cross-tenant conversation transcript leak (read/export/list/SSE) → ownership gate + closed the auto-vivification bypass.
- `tickets.py`, `knowledge.py`, `customers.py`, `dashboard.get_subscription`, `test_whatsapp.py` → auth + company scoping + RBAC.
- Meta/WhatsApp webhooks had **no signature verification** → HMAC-SHA256 + rate limiting (rate limiter now keys on the real socket peer IP, not the attacker-controlled `X-Forwarded-For` header).
- Meta debug/tester/logs routes unauthenticated → auth + super-admin for destructive ops.
- Admin self-lockout risk (role reassignment **and** role-permission editing) → last-admin protection + TOCTOU-safe transaction.
- `automation_policy` silent kill-switch defeat → file AND db (ops ceiling), not override.
- Per-company bot brain: `automation_policy` / `knowledge_manager` / `profile_loader` / `business_connectors` now read per-company DB with safe static-file fallback; `company_id` plumbing verified end-to-end through the real pipeline.
- Facebook OAuth reconnect could silently steal a channel_account (and its conversation history) from another company → fixed.
- Conversations feature (the actual inbox) never checked `conversations.view/reply` RBAC at all → now enforced, additive on top of existing ownership checks.
- AppTable had no real pagination (silently showed unpaginated full lists); ConfirmDialog's cancel-guard was bypassable via backdrop/X-button while an action was in-flight → both fixed.
- Schema-creation race that crashed the app on a fresh DB → fixed.
- `automation_policy`'s file-level ops kill switch (`bot_enabled=false`) previously only gated the AI branch — a "disabled" channel kept auto-replying via the scripted menu/flow state machine. Now `Engine.handle()` checks it at entry (file-level only, not company-scoped) and every caller of `message_gateway.handle_text()` handles a `None` response gracefully instead of crashing.
- `get_reply_flow_steps()` now treats an explicitly-saved empty steps list as "run zero steps" instead of silently substituting the full default sequence.
- `test/whatsapp/` debug endpoint now resolves and passes the caller's real `company_id` instead of always using the default company.
- Legacy `admin/api/app.py` (unauthenticated Flask app, `debug=True`, dead knowledge-manager calls) deleted — confirmed orphaned, zero references anywhere in the repo.
- **Investigated and correctly left unchanged:** `business_connectors.py`'s per-company DB override was flagged as needing the same file-is-a-ceiling AND-combination as `automation_policy.py`, but this was a false premise — its DB row is a genuine per-company opt-in/opt-out that can go either direction from the static file default, and is already covered by a passing test (`test_connectors_uses_db_row_when_configured`) that would break if "fixed". Not a bug.

**Recurring lesson (now standard practice):** several parallel-built fixes landed on worktrees forked from stale/old commits, which caused (a) silent merge duplication that only an AST scan + full test run catches, and (b) whole reimplementations of infra (e.g. AuthContext's permission system) that already existed on the real branch head, requiring careful manual reconciliation, not blind `git merge`. Every merge in this effort is now: resolve conflicts by understanding both sides' intent → `py_compile` + AST dup-arg/kwarg scan → full `pytest` run → frontend lint/vitest/build → only then commit + push. **A second lesson from the final Round-1 repair batch:** even when a background agent is told to "read current code, not an older description," its *worktree itself* can still be silently rooted at a stale ancestor commit (seen repeatedly at `ad14366`) — the agent faithfully reads its own (stale) reality and can reach confidently wrong conclusions (e.g. "this file doesn't exist" for a file that exists on the real branch head). Any finding that contradicts known current state must be independently re-verified against the actual current HEAD before being trusted, not merged on the agent's say-so.

---

## 🔧 IN PROGRESS

Nothing at the moment — Round-1's 23/23 findings are fixed and merged, and all previously-requested features except the items in the table below are built. Next up (not yet started): Triggers, Calls, Catalogue/Scheduler/Appointments/Team Chat, then a genuinely fresh full-codebase audit round.

---

## ❌ NOT BUILT YET — remaining features (user requested: build all, ASAP)

| Feature | State | Notes |
|---|---|---|
| **Triggers (remaining ~23 types)** | ~7 exist | Build the rest on the existing pattern. |
| **Calls page** | not started | Needs a calling provider. **Interim decision: Twilio-style provider abstraction** so it can be swapped; confirm provider before wiring real credentials. |
| **Master Catalogue** | placeholder `ModulePage` | Product catalogue UI. |
| **Scheduler** | placeholder `ModulePage` | Social post scheduling. |
| **Appointments** | placeholder `ModulePage` | Booking module. |
| **Team Chat** | placeholder `ModulePage` | Internal team messaging. |

---

## 🚦 Launch-readiness gate (user requirement)

**Rule:** 5 consecutive full-codebase audit rounds with ZERO confirmed findings.
Any confirmed finding → fix it → counter resets to 0.

**Clean-streak: 0 / 5.**
- Round 1: NOT clean — 23 real findings (0 false positives). **23/23 now fixed and merged.**
- A fresh, full re-audit is needed once all remaining features (Triggers/Calls/Catalogue/Scheduler/Appointments/Team Chat) are also merged — only then does a "clean round" attempt actually count toward the 5. Round 1 does not count toward the streak since it wasn't clean.

---

## Resuming from another machine

1. `git pull origin claude/tzone-release-timeout-fixes-pesy2r`
2. Read this file (top) + `docs/DECISION_LOG.md`.
3. Everything committed here is on GitHub — nothing lives only in a cloud session.
