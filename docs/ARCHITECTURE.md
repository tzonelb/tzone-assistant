# Architecture

## Current architecture

```text
Connected Channels
  ├─ Meta / Messenger
  ├─ WhatsApp
  ├─ Instagram foundation
  └─ Telegram foundation
        │
        ▼
Webhook / Channel Adapters
        │
        ▼
Message parsing and normalization
        │
        ▼
Conversation + AI engine
        │
        ├─ Knowledge matcher
        ├─ Intent/language routing
        ├─ Automation policy
        └─ Human handover
        │
        ▼
FastAPI routes and services
        │
        ▼
SQLite persistence (current local architecture)
        │
        ▼
React / Vite employee workspace
```

## Backend

Canonical entry point: root `main.py`.

Main route groups:

- Authentication
- Dashboard
- Conversations
- Manual messages
- Notifications
- Company settings
- Customers
- Roles and permissions
- Conversation tags
- Developer center
- Knowledge
- Tickets
- Health
- WhatsApp and Meta webhooks

Service layer:

- Authentication
- Conversation control
- Notifications
- Customers
- Company settings
- Diagnostics

## Frontend

- React 19
- Vite 8
- React Router 7
- Material UI
- TanStack Query
- Auth, conversation-live, and notification contexts
- Login, dashboard, conversations, notifications, settings, permissions, comments, and module placeholders

## Persistence

The current implementation uses direct SQLite access with schema creation/upgrade logic in services and `database/database.py`.

### Risks

- distributed schema changes;
- SQLite write concurrency limits;
- no formal migration history;
- production backup/restore not yet formalized.

## Target architecture

```text
Channels
  -> Idempotent webhook adapters
  -> Normalized event envelope
  -> Conversation application service
  -> Atomic state machine / ownership transaction
  -> PostgreSQL
  -> AI or human routing
  -> Outbound channel gateway
  -> Realtime event bus
  -> React workspace / mobile / public API
```

## Recommended evolution

1. Keep a modular monolith until core workflows stabilize.
2. Declare root `main.py` as the only app entry point.
3. Create one migration system.
4. Add PostgreSQL and staging after Patch 9 acceptance.
5. Use a queue for delayed AI work, notifications, imports, and reports.
6. Standardize realtime delivery with reconnect/deduplication semantics.
7. Add structured logs, tracing IDs, error reporting, and metrics.
8. Add tenant-isolation and idempotency tests.
9. Version public APIs before external integrations.

## Architectural principles

- Backend is authoritative.
- Tenant isolation is mandatory.
- State transitions are atomic.
- Webhooks are idempotent.
- Sensitive actions are audited.
- AI is policy-controlled and grounded.
- Frontend never invents ownership or permissions.
- One migration system and one entry point.
- Prefer reversible changes and feature flags.
