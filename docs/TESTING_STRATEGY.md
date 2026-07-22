# Testing Strategy

## Test layers

### 1. Static and syntax

- Python compilation/imports
- Frontend lint/static contract checks
- forbidden path and secret scans

### 2. Unit/domain

- state transition rules
- permission decisions
- notification grouping
- search/filter logic
- tenant boundaries

### 3. Database/integration

- fresh database startup
- schema upgrades and deduplication
- transaction atomicity
- concurrent takeover
- idempotent webhook handling
- per-user notification state

### 4. API

- authentication and authorization
- 409 conflict behavior
- owner/admin endpoints
- read/unread
- release, transfer, and Return-to-AI
- pagination/search/counters

### 5. Frontend build and component behavior

- production Vite build
- stale request guards
- permissions rendered from backend snapshots
- bell list separate from Notification Center
- dark mode readability

### 6. End-to-end manual QA

Use three independent sessions:

- Moetaz
- Test
- Admin

Required scenarios:

1. Moetaz takes an unowned conversation.
2. Test sees read-only state.
3. Test's stale takeover receives 409.
4. Owner remains Moetaz after refresh/reconnect.
5. Moetaz replies and lease renews.
6. Test cannot reply or modify read state.
7. Admin transfers/releases/returns to AI.
8. Timeline records all actions.
9. AI reply appears in Timeline but not bell.
10. Bell shows at most five unread cards.
11. Clear shown clears only displayed IDs for that employee.
12. Another employee's notification state remains unaffected.
13. Search finds an older message body.
14. Channel unread counters update.
15. Dark theme remains readable.
16. Real Messenger incoming/outgoing path remains operational.

## Regression rule

Every discovered defect becomes a permanent automated or documented manual regression test before acceptance.
