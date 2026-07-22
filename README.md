# T-ZONE CRM + AI Platform

> An omnichannel customer-operations, CRM, AI-assistance, commerce, service, and analytics platform for T-ZONE.

## العربية — ملخص سريع

منصة **T-ZONE CRM + AI Platform** ليست مجرد Chatbot. الهدف هو جمع محادثات العملاء، الذكاء الاصطناعي، CRM، الموظفين، المهام، المبيعات، المخزون، المحاسبة، الصيانة، IPTV، التقارير، والفروع ضمن نظام واحد قابل للتوسع.

الحالة الحالية: الأساسات موجودة، لكن **Patch 9.1 الخاص بدورة المحادثة والملكية والإشعارات لم يُقبل إنتاجيًا بعد**. الكود الخاص به أصبح Code Complete ضمن حزمة منفصلة، لكن ما زال يحتاج تطبيقًا آمنًا، Frontend build، Manual QA بثلاثة حسابات، ثم Commit/PR/Merge.

## Product vision

T-ZONE should become the operating system of a technology retail and service company:

- Omnichannel conversations: Messenger, WhatsApp, Instagram, Telegram, and website chat.
- Company-grounded AI with human handover and department routing.
- Unified CRM and complete customer history.
- Employees, roles, permissions, branches, and audit history.
- Tasks, reminders, follow-ups, team chat, and scheduling.
- Catalogue, inventory, orders, payments, invoices, and accounting.
- Repairs, warranty, technicians, IPTV, telecom services, and renewals.
- Analytics, automation, mobile applications, public API, and future SaaS support.

## Current official repository state

| Item | Value |
|---|---|
| Secure baseline commit | `b7212114854d5c6f84fea31d1bf5ca912348694c` |
| Stable branch | `main` |
| Active recovery branch | `patch/9-1-conversation-workflow-recovery` |
| Patch 9 implementation artifact | Code-complete snapshot commit `0554e322ad2565e00f116f848a1af52b381a6149` |
| Patch 9 merge status | Not installed, not pushed, not accepted, not merged |
| Production readiness | Blocked until Patch 9 manual acceptance and release checks pass |

See [docs/PATCH_9_STATUS.md](docs/PATCH_9_STATUS.md) for the exact distinction between the GitHub baseline and the deferred code-complete artifact.

## Technology stack

### Backend

- Python / FastAPI `0.139.0`
- Uvicorn `0.49.0`
- Pydantic `2.13.4`
- Direct `sqlite3` persistence with WAL in the current development architecture
- JWT authentication
- Channel adapters for Meta/Messenger, WhatsApp, Instagram foundation, and Telegram foundation

### Frontend

- React `19.2.x`
- Vite `8.1.x`
- React Router `7.18.x`
- Material UI `9.2.x`
- TanStack Query `5.101.x`
- Axios / Fetch API client
- Recharts

## Canonical entry point

The current canonical backend application is:

```powershell
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

`backend/main.py` is legacy/incomplete relative to root `main.py` and must not be used as the production entry point unless an explicit architecture decision replaces it.

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

## Documentation map

Start here:

1. [PROJECT_MASTER.md](PROJECT_MASTER.md) — complete product, technical, and execution reference.
2. [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) — what works, what is blocked, and what remains.
3. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — current and target architecture.
4. [docs/ROADMAP.md](docs/ROADMAP.md) — phased product roadmap.
5. [docs/CHECKPOINTS.md](docs/CHECKPOINTS.md) — checkpoint workflow, required artifacts, and outcomes.
6. [docs/ONBOARDING.md](docs/ONBOARDING.md) — developer onboarding.
7. [docs/DEVELOPMENT_WORKFLOW.md](docs/DEVELOPMENT_WORKFLOW.md) — Git, patches, QA, and release law.
8. [docs/CONVERSATION_STATE_MACHINE.md](docs/CONVERSATION_STATE_MACHINE.md) — conversation ownership and AI/human rules.
9. [docs/LAUNCH_READINESS.md](docs/LAUNCH_READINESS.md) — release gate checklist.
10. [AGENTS.md](AGENTS.md) — mandatory instructions for AI coding agents and developers.

## Non-negotiable rules

- Backend is the source of truth for conversation ownership and permissions.
- One employee owns a conversation at a time.
- AI must stop when a human owns the conversation.
- Timeline is mandatory and separate from employee notifications.
- AI replies appear in Timeline but do not create bell notifications.
- Never modify the working Messenger webhook path without an explicit scoped requirement and regression test.
- Never commit `.env`, tokens, runtime databases, customer conversations, backups, or generated build folders.
- Build success is not acceptance. Multi-user manual QA is mandatory.
- No patch is called Final before explicit acceptance.

## License and ownership

This repository contains T-ZONE business software and project documentation. Repository visibility does not grant permission to reuse private business logic, branding, customer data, or credentials. Add a formal license before accepting external contributions.
