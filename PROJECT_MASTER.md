# T-ZONE CRM + AI Platform — Master Project Guide

**Document version:** 2026-07-31
**Purpose:** Single onboarding and continuity reference for owners, developers, contractors, and AI coding agents.
**Active branch:** `fix/release-timeout-and-channel-fixes`
**Core principle:** Plug-and-play channel connection (real OAuth, under 2 minutes, zero technical steps for the customer), multi-tenant, backend-owned authorization.

---

## 1. What this project is

T-ZONE CRM + AI Platform is a modular company operating platform for a technology retail and service business. It begins with omnichannel customer conversations and grows into a unified system for customer relationships, employees, operations, sales, stock, finance, repairs, subscriptions, and analytics.

It is not intended to be only:

- a chatbot;
- a social media inbox;
- a basic POS;
- or a standalone CRM.

The long-term product combines capabilities normally spread across customer support, CRM, ERP, helpdesk, project management, and AI automation tools, while remaining customized for T-ZONE operations in Lebanon and future branches or companies.

## 2. Business vision

The desired end-to-end flow is:

1. A customer contacts T-ZONE through Messenger, WhatsApp, Instagram, Telegram, or website chat.
2. The platform normalizes and stores the message.
3. The customer is matched to a unified CRM profile.
4. The system detects language, intent, department, and urgency.
5. Company-grounded AI replies when policy permits.
6. A human employee takes control when required.
7. Exactly one employee owns the conversation at a time.
8. Management can transfer, release, override, or return the conversation to AI.
9. Every meaningful action is recorded in a Timeline and audit trail.
10. The conversation can create a lead, task, order, payment, invoice, repair, warranty case, IPTV renewal, telecom service, appointment, or follow-up.
11. Managers receive operational and financial analytics.

## 3. Primary users

- **Company owner / Super Admin:** full company, branch, employee, settings, security, and reporting control.
- **Administrator / Manager:** assignment override, reporting, permissions, department supervision, and workflow control.
- **Employee / Agent:** conversations, assigned work, customer data allowed by role, tasks, notes, orders, and service actions.
- **Technician:** repair tickets, diagnostics, parts, warranty status, and customer updates.
- **Accountant / Cashier:** payments, expenses, receivables, invoices, closing, and reports.
- **AI assistant:** company-grounded assistance operating under explicit channel and department policies.
- **Customer:** communicates through connected channels and, later, a customer portal/mobile experience.

## 4. Product pillars

### 4.1 Omnichannel conversations

- Messenger
- WhatsApp
- Instagram
- Telegram
- Website chat
- Future email and voice channels

### 4.2 AI and knowledge

- Company knowledge base
- Intent and language detection
- Department routing
- Confidence-based escalation
- Human handover
- AI teaching center
- Response policies
- Attachments and semantic search
- AI quality and cost analytics

### 4.3 CRM

- Unified customer profile
- Channel identities
- Conversation history
- Notes and tags
- Segments
- Leads/opportunities
- Follow-up history
- Orders, repairs, subscriptions, and payments linked to the customer

### 4.4 Operations and collaboration

- Tasks and reminders
- Follow-ups
- Team chat and mentions
- Internal comments and files
- Scheduler and appointments
- Department queues
- Employee presence and workload

### 4.5 Commerce and finance

- Catalogue and pricing
- Products and variants
- Inventory and stock movements
- Suppliers and purchasing
- Orders and fulfilment
- Invoices and payments
- Cashbox, expenses, receivables, payables, and closing

### 4.6 Service operations

- Repairs and diagnostics
- Technician assignment
- Parts and cost tracking
- Warranty
- Customer status notifications
- IPTV subscriptions and renewals
- Telecom recharge/services

### 4.7 Platform and analytics

- Multi-company / multi-branch / multi-tenant
- Roles and permissions
- Audit logs
- Operational KPIs
- Employee performance
- AI performance
- Monitoring, backups, staging, and production deployment
- Public API, plugins, and mobile apps

## 5. Current implementation

### Backend foundations

- FastAPI application
- JWT authentication
- Protected APIs
- Roles and permission foundations
- Company/workspace/branch foundations
- Dashboard APIs
- Conversations and manual replies
- Notification foundation
- Customer foundation
- Company settings
- Knowledge and AI foundations
- Tickets and developer-center foundations
- Meta/Messenger webhook integration
- WhatsApp, Instagram, and Telegram foundations

### Frontend foundations

- React/Vite dashboard
- Authentication context and protected routes
- Dashboard and navigation
- Conversation inbox and detail
- Notification dropdown and Notification Center
- Company settings
- Roles and permissions
- Comments and UI settings
- Placeholder modules for later phases

### AI foundations

- Intent detector
- AI router
- Knowledge matcher/manager
- Conversation memory/store
- Prompt builder
- Response policies
- Business modules/connectors
- Language handling
- AI/human automation policies

### Persistence

The active baseline uses direct `sqlite3` access and WAL mode. SQLite is acceptable for local development and early testing. Production architecture should move toward PostgreSQL with one formal migration system after the conversation core is stable.

## 6. Current module status

The conversation core (single-owner control, HTTP 409 conflict handling, AI/human state protection, Release / Return-to-AI, Timeline, read/unread, server-side search, notification behavior) is built and in active use — it is no longer a pending patch. The platform has since grown well beyond it. Runtime secrets, `.env`, databases, customer conversation data, virtual environments, node modules, generated builds, and local backups are excluded from Git.

### Built and working
- **Conversations** — real-time omnichannel inbox, AI handling, human takeover, department routing, internal notes with @mentions, single-owner ownership model.
- **CRM / Contacts** — unified profiles, channel identities, assigned employee, and a per-customer Client File / Timeline.
- **AI Teaching** — split into Instructions / Knowledge / Train & Test, scoped per department and per channel, with a live "Test your AI" panel running the real reply pipeline.
- **Broadcast** — bulk messages with image/video/audio, number-list targeting, delivery report.
- **Tasks** — typed tasks with optional conversation deep-link.
- **Appointments** — per-employee visibility (owner/admin see all), server-enforced.
- **Team Chat** — internal employee messaging with mentions.
- **Master Catalogue** — CSV import and WhatsApp Catalogue import.
- **Community / Publish** — Buffer-style social scheduler: Home dashboard, per-channel sidebar, Create Post modal with per-network customization (Post / Reel / Story), real Facebook/Instagram Graph API publishing, background due-post worker.
- **Saved Replies** — department-scoped, admin-managed, employee-usable.
- **Chatbot Control** — consolidated bot-behavior settings (greeting, who-replies-first, reply access, return-to-AI timeout); auto-read is always-on and no longer a toggle.
- **Sign-up** — public self-registration creating company + owner role + owner user + trialing subscription, with plan selection.
- **Security** — login lockout, PBKDF2-SHA256 hashing, Fernet-encrypted channel tokens, Caddy security headers, disk-encryption deployment guidance.
- **Channel connect** — real Facebook/Instagram OAuth popup flow (plug-and-play).
- **Deployment scaffolding** — Docker + Caddy (`Dockerfile`, `frontend/Dockerfile`, `docker-compose.yml`, `DEPLOYMENT.md`).

### Built but pending an external step
- **Comments / unified community inbox** — buildable and largely designed; the Meta feed-comment webhook needs a public HTTPS callback URL, which only exists once the platform is deployed to a real domain. The `instagram_manage_comments` permission and webhook subscription are configured once at the platform level (not per customer).
- **Broadcast/Comments media over real providers** — needs `PUBLIC_BACKEND_URL` set to the deployed HTTPS domain.

### Paused pending an owner decision
- **Real Calls** (live inbound/outbound + transfer) — requires a telephony provider (Twilio or equivalent) account and number. WhatsApp/Messenger/Instagram have no public voice-call API, so this cannot be done through the social channels.

## 7. Conversation operating model

The platform must implement these canonical states:

- `AI_ACTIVE`
- `HUMAN_QUEUE`
- `HUMAN_OWNED`
- `DONE`
- `ARCHIVED`

Key rules:

- Everyone with permission may view a conversation.
- Only one employee may own and reply.
- A normal non-owner is read-only.
- Administrators can override, transfer, release, and return to AI.
- AI stops when a human owns the conversation.
- A stale takeover returns HTTP 409 and never changes the current owner.
- Timeline records customer, AI, employee, ownership, status, read-state, note, and assignment events.
- AI reply is a Timeline event, not a bell notification.

See `docs/CONVERSATION_STATE_MACHINE.md`.

## 8. Current technical debt

### P0 — release blockers

- Not yet deployed to a public HTTPS domain — required before Comments webhooks and real media delivery work end-to-end.
- No formal multi-user acceptance QA run recorded on the current branch (build passing ≠ accepted).
- Launch rollback and production smoke-test procedures not yet exercised on a real VPS.

### P1 — near-term architecture debt

- Multiple backend entry points exist; root `main.py` must remain canonical and the legacy entry point should be removed or archived after verification.
- Direct SQLite schema changes are spread across services; adopt a single migration mechanism.
- Realtime behavior mixes polling/SSE concepts; standardize one strategy.
- Automated end-to-end browser testing is missing.
- Staging and production environments are not fully separated.
- Observability, structured logging, error tracking, and operational alerts need formalization.

### P2 — scaling debt

- PostgreSQL migration
- Queue/event worker for long-running tasks
- Object storage for attachments
- API versioning
- Tenant-isolation tests
- Data retention and deletion policies
- Backup restore drills
- Feature flags
- Performance/load testing

## 9. Delivery roadmap

### Phase 9 — Conversation stabilization ✅

Single-owner conversation core, Timeline, notifications, and takeover model are built and in use.

### Phase 10 — Workspace operations

Tasks, follow-ups, team chat, mentions, files, scheduler, and appointments.

### Phase 11 — CRM

Unified customer profiles, channel identities, tags, segments, opportunities, and customer timeline.

### Phase 12 — AI and automation

Knowledge center, attachments, semantic search, confidence/escalation, department automation, and AI analytics.

### Phase 13 — Commerce

Catalogue, products, pricing, suppliers, inventory, stock movements, orders, invoices, and payments.

### Phase 14 — Accounting

Income, expenses, cashbox, receivables/payables, closing, and reports.

### Phase 15 — Service operations

Repairs, warranty, technicians, IPTV, telecom services, renewals, and customer updates.

### Phase 16 — Production and analytics

PostgreSQL, deployment, monitoring, backups, audit hardening, KPIs, performance, and production readiness.

### Future

Customer portal, mobile apps, voice AI, vision AI, public API, plugins, SaaS billing, and external integrations.

## 10. Development law

1. Read the current file before modifying it.
2. Use the exact current Git commit as the source of truth.
3. Never invent APIs, database fields, routes, or file state.
4. Preserve working Messenger behavior unless the scope explicitly requires a change.
5. Backend owns authorization, ownership, and workflow state.
6. Database, backend, frontend, and tests change together.
7. Every bug receives a regression test.
8. Never commit secrets or runtime/customer data.
9. No patch-on-patch hotfix chain; prefer one cumulative tested change.
10. Build success is not user-workflow acceptance.
11. Manual QA requires two employees and one administrator.
12. Never call a release Final before explicit acceptance.
13. Update documentation and checkpoint records after every accepted stage.

## 11. Checkpoint model

A checkpoint is not only a backup. It is a reproducible statement of:

- exact commit and branch;
- what is working;
- what is incomplete;
- architecture and decisions;
- files changed;
- tests passed/failed;
- database/migration state;
- security state;
- launch risk;
- next approved scope.

Checkpoints progress through discovery, secure baseline, implementation, verification, manual acceptance, release, and post-release review. See `docs/CHECKPOINTS.md`.

## 12. Definition of done for a phase

A phase is complete only when:

- scope and acceptance criteria are documented;
- code and migrations are complete;
- automated tests pass;
- frontend production build passes;
- security and forbidden-file scans pass;
- manual QA passes;
- rollback is proven;
- documentation is updated;
- owner explicitly accepts;
- commit, PR, merge, and tag are complete;
- production smoke checks pass when applicable.

## 13. Recommended first actions for a newly hired developer

1. Read `README.md`, this file, `AGENTS.md`, and `docs/PROJECT_STATUS.md`.
2. Clone the repository and verify the exact branch and commit.
3. Set up `.env` locally from `.env.example` without sharing values.
4. Run backend and frontend locally.
5. Read the conversation state machine and Patch 9 acceptance specification.
6. Do not redesign or add new modules before Patch 9 acceptance.
7. Reproduce the current workflow with two employee sessions and one admin session.
8. Document findings before changing code.
9. Add regression tests for every confirmed bug.
10. Work through a Pull Request with a clear manual QA report.
