# Changelog

## Unreleased — Patch 9.1 Conversation Workflow Recovery

Status: code-complete artifact exists, installation and acceptance deferred.

Planned/implemented in the deferred artifact:

- Atomic one-owner conversation control
- HTTP 409 stale takeover response
- Owner heartbeat and lease renewal
- AI/human state race protection
- Release, transfer, and Return-to-AI transitions
- Timeline restoration
- Read/unread authorization
- Assigned-to-me and unread server-side behavior
- Full-message search
- Unread channel counters
- Five-card bell and exact Clear shown
- Per-user notification read/delete isolation
- Dark-theme workflow improvements
- Regression tests and CI foundation

## 2026-07-21 — Secure baseline

- Reinitialized repository history without secrets/runtime data.
- Added `.gitignore`, `.gitattributes`, and `.env.example`.
- Established secure baseline commit `b7212114854d5c6f84fea31d1bf5ca912348694c`.
- Created protected recovery branch `patch/9-1-conversation-workflow-recovery`.
- Enabled GitHub Secret Protection and Push Protection.

## Historical foundations

- FastAPI backend and JWT authentication
- React/Vite frontend
- Messenger integration
- AI and knowledge foundation
- Conversation inbox/detail
- Notifications foundation
- Company settings
- Roles and permissions
- Customer and tenant foundations
- Patch 7–8 conversation/notification UI work
- Patch 8.1 and Patch 9 stabilization attempts

Historical Patch 9 attempts passed partial automated checks but failed manual workflow acceptance. They are not considered accepted releases.
