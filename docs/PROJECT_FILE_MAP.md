# Project File Map

## Root

- `main.py` — canonical FastAPI application entry point.
- `requirements.txt` — Python dependencies.
- `.env.example` — safe environment variable names only.
- `README.md` — repository entry documentation.
- `PROJECT_MASTER.md` — full project reference.

## Backend

- `backend/api/routes/` — HTTP API routes.
- `backend/api/schemas/` — Pydantic request/response schemas.
- `backend/services/` — application/domain services.
- `backend/main.py` — legacy/incomplete entry point; do not use as canonical.

High-risk conversation files:

- `backend/api/routes/conversations.py`
- `backend/api/routes/manual_messages.py`
- `backend/api/routes/notifications.py`
- `backend/services/conversation_control_service.py`
- `backend/services/notification_service.py`
- `database/database.py`

## Channels

- `channels/meta/` — Meta/Messenger parser, processor, sender, webhook, logs, and testing helpers.
- `channels/whatsapp/` — WhatsApp webhook/sender/session.
- `channels/instagram/` — Instagram foundation.
- `channels/telegram/` — Telegram bot foundation.
- `gateway/message_gateway.py` — outbound message gateway.

These channel files are protected during Patch 9 unless explicitly included in scope.

## AI and business engine

- `core/` — AI router, knowledge matcher, memory, prompt, policies, intent, session, and business routing.
- `config/` — AI/business policies, profile, knowledge, and application settings.
- `features/` — domain-specific flow/service foundations.

## Database

- `database/database.py` — current database foundations and table management.
- `database/tzone.db` — runtime database, ignored and never committed.

## Frontend

- `frontend/src/api/client.js` — API client.
- `frontend/src/contexts/` — authentication, conversation live state, notifications.
- `frontend/src/pages/conversations/` — inbox/detail and workflow UI.
- `frontend/src/components/notifications/` — bell dropdown.
- `frontend/src/pages/notifications/` — full Notification Center.
- `frontend/src/styles/` — shared theme and UI styles.

## Tools

- `tools/create_super_admin.py`
- `tools/migrate_auth_schema.py`
- `tools/test_openai.py`

## Runtime paths never committed

- `.env`
- `.venv/`
- `frontend/node_modules/`
- `frontend/dist/`
- `database/*.db*`
- `data/conversations/`
- private uploads
- logs, backups, and patch artifacts
