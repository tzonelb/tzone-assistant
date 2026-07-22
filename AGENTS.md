# AGENTS.md — Mandatory Project Instructions

This file applies to human developers and AI coding agents working on T-ZONE.

## Read first

Before changing code, read:

1. `PROJECT_MASTER.md`
2. `docs/PROJECT_STATUS.md`
3. `docs/ARCHITECTURE.md`
4. `docs/DEVELOPMENT_WORKFLOW.md`
5. `docs/CONVERSATION_STATE_MACHINE.md`
6. The active patch acceptance specification

## Source of truth

- Use the current Git branch and exact commit.
- Read complete current files before editing.
- Do not use old ZIPs, remembered snippets, or reconstructed files as authoritative.
- Current secure baseline: `b7212114854d5c6f84fea31d1bf5ca912348694c`.
- Patch 9 code-complete artifact commit `0554e322...` is external/deferred until safely applied and accepted.

## Protected behavior

Do not modify the working Messenger integration unless the approved scope explicitly requires it:

- Meta/Messenger webhook
- parser
- processor
- sender
- outbound gateway

Any authorized change requires an integration regression test.

## Conversation rules

- Backend is authoritative.
- Exactly one employee owns a conversation.
- Non-owner employees are read-only.
- Admin override is explicit.
- AI stops under human ownership.
- Timeline is mandatory.
- AI replies are Timeline events, not bell notifications.
- Ownership conflict returns HTTP 409.

## Delivery rules

- Complete files only; no unsafe partial patches for production changes.
- Update backend, frontend, persistence, and tests together.
- Add a regression test for each bug.
- No `FINAL` label before manual acceptance.
- Do not commit secrets, `.env`, databases, customer conversations, backups, `node_modules`, `.venv`, or build outputs.
- Stop on source mismatch; never blindly overwrite newer files.
- Manual QA requires two employee accounts and one administrator.
- Update checkpoint docs after acceptance.
