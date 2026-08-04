# T-ZONE Platform — Live Status

> **This file is the single source of truth for "where are we / what's left".**
> It is updated and pushed with every batch of work so any machine or session
> can `git pull` and immediately know the current state. If you are resuming
> from a different machine: `git pull origin claude/tzone-release-timeout-fixes-pesy2r`
> then read this file top to bottom.

**Last updated:** 2026-08-04 (session running as Claude)
**Active branch:** `claude/tzone-release-timeout-fixes-pesy2r`
**Run command:** `uvicorn main:app` (root `main.py` is canonical — `backend/main.py` is legacy/dead, see docs/DECISION_LOG.md D-001)
**Target launch:** Thursday 2026-08-06

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
- Company Settings tabs shown/editable without permission → real per-section permission gating (frontend + backend).
- Dead "Configure" button → real generic per-section settings editor.
- Channels description said 6 channels, only 4 real → corrected + honest copy.
- Notification filter silently cleared on background refresh → race fixed in NotificationContext.
- Customer/conversation detail silent overwrite → optimistic-concurrency (control_version CAS) + 409 handling.
- Reply "reply_mode" setting saved but never read by engine → engine now honors it.

### Missing features that were built from scratch
- **Broadcast** — compose, accurate recipient count, resume-safe send (idempotent, double-send-guarded), history + live progress.
- **Facebook OAuth connect** — real connect/callback flow, Fernet-encrypted per-company Page/IG tokens, inbound message company-routing, frontend Connect button + result banner.
- **Reply Flow Builder** — step editor with toggle/reorder + a clearly-labeled Escalation condition (Yes→human / No→AI), wired into the engine.
- **Theme Studio** — working "Heading scale" control applied via CSS variable.

### Security hardening (audit sweep #1 — 26 findings, all fixed)
- Cross-tenant conversation transcript leak (read + export + list/SSE) → company-ownership gate + closed the auto-vivification bypass.
- `tickets.py` fully unauthenticated + unscoped → auth + company scoping + RBAC.
- `knowledge.py` broken (called nonexistent methods) → real company-scoped CRUD.
- `dashboard.get_subscription` missing permission check → added.
- `customers.py` no RBAC → permission-gated.
- `test_whatsapp.py` unauthenticated → auth required.
- Meta/WhatsApp webhooks had **no signature verification** → HMAC-SHA256 (X-Hub-Signature-256) + rate limiting.
- Meta debug/tester/logs routes unauthenticated → auth + super-admin for destructive ops.
- Admin self-lockout risk (role reassignment) → last-admin protection.
- `automation_policy` silent kill-switch defeat → file AND db (ops ceiling).
- Per-company bot brain: `automation_policy` / `knowledge_manager` / `profile_loader` / `business_connectors` now read per-company DB with safe static-file fallback.
- Schema-creation race that crashed on a fresh DB → fixed.

---

## 🔧 IN PROGRESS

- **Round-1 audit fixes (23 findings)** — fix workflow running; not yet merged.
  Key items: conversations feature never checks `conversations.view/reply`;
  role-permission-edit self-lockout + TOCTOU race; reply_flow empty-list
  mishandling; kill-switch scope; business_connectors OR-vs-AND; OAuth
  cross-tenant page hijack; legacy `admin/` Flask app removal; several
  frontend component/data-quality fixes.

---

## ❌ NOT BUILT YET — remaining features (user requested: build all, ASAP)

| Feature | State | Notes |
|---|---|---|
| **Analytics (BI report)** | placeholder `ModulePage` | Needs real metrics page from existing DB data (channels/employees/AI/customers). |
| **Triggers (remaining ~23 types)** | ~7 exist | Build the rest on the existing pattern. |
| **Calls page** | not built | Needs a calling provider. **Interim decision: Twilio-style provider abstraction** so it can be swapped; confirm provider before wiring real credentials. |
| **Customers page** | placeholder | Real customer DB UI (backend `customers.py` exists). |
| **Master Catalogue** | placeholder | Product catalogue UI. |
| **AI Teaching** | placeholder | Instructions/knowledge management UI. |
| **Tasks** | placeholder | Task/follow-up management. |
| **Scheduler** | placeholder | Social post scheduling. |
| **Appointments** | placeholder | Booking module. |
| **Team Chat** | placeholder | Internal team messaging. |

---

## 🚦 Launch-readiness gate (user requirement)

**Rule:** 5 consecutive full-codebase audit rounds with ZERO confirmed findings.
Any confirmed finding → fix it → counter resets to 0.

**Clean-streak: 0 / 5.**
- Round 1: NOT clean — 23 real findings (0 false positives). Fixes in progress.

---

## Resuming from another machine

1. `git pull origin claude/tzone-release-timeout-fixes-pesy2r`
2. Read this file (top) + `docs/DECISION_LOG.md`.
3. Everything committed here is on GitHub — nothing lives only in a cloud session.
