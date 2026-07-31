# T-ZONE CRM + AI Platform

> An omnichannel customer-operations, CRM, AI-assistance, publishing, commerce, and analytics platform for T-ZONE.

## العربية — ملخص سريع

منصة **T-ZONE** هي نظام تشغيل متكامل لشركة تجارة وخدمات تقنية، وليست مجرد Chatbot. تجمع محادثات العملاء عبر كل القنوات، الذكاء الاصطناعي المبني على معرفة الشركة، الـ CRM، الموظفين والصلاحيات، المهام والمواعيد، النشر على السوشال ميديا (بأسلوب Buffer)، الكتالوج والمخزون، والتقارير — ضمن منصة واحدة قابلة للتوسع ومتعددة الشركات (multi-tenant).

الفلسفة الأساسية: **Plug and play** — أي شركة تشبك قنواتها (فيسبوك، انستغرام، واتساب…) بأقل من دقيقتين عبر تسجيل دخول رسمي (OAuth)، بدون أي خطوة تقنية أو webhook أو مفاتيح API يدوية.

## Product vision

T-ZONE aims to be the operating system of a technology retail and service company:

- **Omnichannel conversations** — Messenger, WhatsApp, Instagram, Telegram, website chat, and a growing list of business channels.
- **Company-grounded AI** with human handover, department routing, and confidence-based escalation.
- **Unified CRM** and complete customer history.
- **Employees, roles, permissions, branches, and audit history.**
- **Tasks, reminders, follow-ups, team chat, and appointments.**
- **Social publishing & community** — schedule posts across channels with per-network customization (Buffer-style), plus a unified comment inbox.
- **Catalogue, inventory, orders, and analytics.**
- **Self-serve signup, plans, and billing** for going public as a SaaS.

## Technology stack

### Backend
- Python / FastAPI, Uvicorn, Pydantic
- Direct `sqlite3` persistence with WAL (development); PostgreSQL is the target for production scale
- JWT authentication; PBKDF2-SHA256 password hashing; Fernet-encrypted channel tokens at rest
- Channel adapters for Meta/Messenger, WhatsApp, Instagram, and Telegram
- Real Facebook/Instagram OAuth connect flow and Graph API publishing

### Frontend
- React + Vite
- React Router
- Material UI icons
- Shared component kit under `frontend/src/components/common/`

## Canonical entry point

The canonical backend application is root **`main.py`**:

```powershell
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

`backend/main.py` is legacy and must not be used as the production entry point.

## Local development

### Backend
```powershell
cd C:\PROJECTS\tzone-assistant
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

### Frontend
```powershell
cd C:\PROJECTS\tzone-assistant\frontend
npm ci
npm run dev
```

Default local frontend API target: `http://127.0.0.1:8000`.

### Tests
```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -q
cd frontend; npm run build
```

## Module status (high level)

See **[PROJECT_MASTER.md](PROJECT_MASTER.md)** for the full, current breakdown of what is built, what is pending an external step (deployment / provider account), and what remains on the roadmap.

Working today: Conversations + AI handling, CRM/Contacts, Client timeline, AI Teaching (Instructions/Knowledge/Test), Broadcast with media, Tasks, Appointments, Team Chat, Master Catalogue import, Community/Publish (Buffer-style scheduler), Saved Replies, Chatbot Control, self-serve Sign-up, login lockout security, and Docker/Caddy deployment scaffolding.

## Non-negotiable rules

- Backend is the source of truth for conversation ownership and permissions.
- One employee owns a conversation at a time; AI must stop when a human owns it.
- Timeline is mandatory and separate from employee notifications; AI replies appear in Timeline but do not create bell notifications.
- Connecting a channel must stay **plug-and-play** — real OAuth, never manual token/webhook entry by the customer.
- Never modify the working Messenger webhook path without an explicit scoped requirement and regression test.
- Never commit `.env`, tokens, runtime databases, customer conversations, backups, or generated build folders.
- Build success is not acceptance. Multi-user manual QA is required before calling a feature done.

## Documentation map

1. [PROJECT_MASTER.md](PROJECT_MASTER.md) — complete product, module-status, and roadmap reference.
2. [DEPLOYMENT.md](DEPLOYMENT.md) — VPS + Docker + Caddy deployment runbook.
3. [SECURITY.md](SECURITY.md) — security posture and threat model.
4. [AGENTS.md](AGENTS.md) — instructions for AI coding agents and developers.
5. [docs/](docs/) — architecture, conversation state machine, workflow, and checkpoints.

## License and ownership

This repository contains T-ZONE business software and documentation. Repository access does not grant permission to reuse private business logic, branding, customer data, or credentials.
