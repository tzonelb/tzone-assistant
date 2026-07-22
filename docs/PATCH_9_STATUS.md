# Patch 9 Status

## Objective

Stabilize conversation ownership, AI/human handover, Timeline, read state, inbox behavior, notifications, search, counters, and dark-theme workflow without adding unrelated business modules.

## Official repository baseline

`b7212114854d5c6f84fea31d1bf5ca912348694c`

## Deferred code-complete artifact

Artifact commit:

`0554e322ad2565e00f116f848a1af52b381a6149`

This commit identifies an assembled source snapshot outside the official GitHub branch. It must not be described as merged, deployed, or accepted.

## Implemented in the artifact

- Atomic ownership and unique conversation identity
- HTTP 409 for stale takeover
- Owner/admin backend authorization
- Lease heartbeat and reply renewal
- AI completion race protection
- Release and Return-to-AI
- Timeline integration
- Read/unread authorization
- Assigned-to-me and Unread server-side behavior
- Search across stored message bodies
- Unread channel counters
- Five-card unread notification bell
- Exact Clear shown IDs
- Notification Center/bell list separation
- Per-user notification read/delete isolation
- Stale frontend request sequence protection
- Dark-theme workflow fixes
- Backend, API, fresh-start, workflow, and static tests
- Messenger protected files unchanged

## Tests reported as passed on the assembled artifact

- Python compilation
- Fresh database startup/schema
- Legacy conversation identity deduplication
- Two-employee ownership conflict
- HTTP 409 behavior
- Owner/admin authorization
- Heartbeat and reply lease renewal
- AI race protection
- Release and Return-to-AI
- Read/unread
- Timeline contract
- Message search contract
- Notification bell contract
- Per-user notification isolation
- Static frontend checks

## Remaining acceptance gates

- Apply artifact to the real recovery branch safely.
- Run frontend Vite production build.
- Run all checks on the installed working tree.
- Manual QA with two employees and one admin.
- Real Messenger smoke test.
- Confirm rollback.
- Owner acceptance.
- Commit/push/PR/merge/tag.

## Definition of accepted Patch 9

Patch 9 is accepted only when all automated and manual gates pass and the repository contains the accepted code through a reviewed merge. Until then, the status is **Code Complete — Installation and Acceptance Deferred**.
