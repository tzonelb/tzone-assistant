# Launch Readiness Checklist

The system is launch-ready only when every P0 item is complete.

## Source and security

- [ ] Final candidate is on the approved branch and exact commit.
- [ ] Working tree is clean.
- [ ] No secrets or forbidden runtime files are tracked.
- [ ] Production tokens were rotated after any prior exposure.
- [ ] Branch protection, Secret Protection, and Push Protection are active.

## Security — verified by test

Each line below is held by an automated test, so it stays true rather than
being true on the day somebody checked. Names are the tests that hold it.

- [x] A locked account cannot be opened with the correct password, and a
      locked account is told it is locked. `test_account_recovery.py`
- [x] Failed attempts against one employee do not lock a colleague sharing the
      same office address — the defect the separate counters exist to prevent.
      `test_locking_one_employee_does_not_lock_another`
- [x] The address threshold stays above the account threshold. Lowering it to
      "tighten security" reintroduces the collateral lockout, so the invariant
      is asserted rather than assumed.
- [x] A reset link works exactly once, expires, and issuing a new one spends
      the previous. The raw token is never stored.
- [x] A reset ends every existing session immediately.
- [x] A forced password change is refused by every route except changing the
      password and asking who you are.
- [x] An administrator cannot reset somebody in another company. `users` is a
      shared table and the id comes from the URL.
- [x] A reset is refused with a usable message when email is not configured,
      rather than reporting a success nobody will receive.
- [x] The inbox does not return a colleague's email, phone or role to an
      employee holding only `conversations.view`. `test_response_shape.py`
- [x] The dashboard does not return the subscription price or the Meta and
      WhatsApp routing identifiers.
- [x] A new column on `users` is not published to a browser by default —
      `sanitize_user` is an allow-list.
- [x] Creating a role stores its permissions, and a duplicate code is still
      reported as a duplicate. `test_roles_admin.py`
- [x] Every response carries the security headers, including refusals.
      `test_security_headers.py`
- [x] A company cannot read another company's data. `test_tenant_isolation.py`
- [x] A platform token cannot reach a company, and a company token cannot
      reach the console. `test_session_scope.py`, `test_platform_admin.py`
- [x] A module switched off by the operator closes its API for that company
      and nobody else. `test_module_gating.py`

## Security — manual, before launch

These cannot be tested from Python and must be checked against the real
deployment:

- [ ] `sudo nginx -t` passes with the rate-limit zones and the security-header
      snippet installed.
- [ ] `curl -I https://YOUR.DOMAIN/` returns all six security headers, and so
      does `curl -I https://YOUR.DOMAIN/index.html` — the second is the one
      that was silently missing them.
- [ ] A request carrying a forged `X-Forwarded-For` does not change the
      address recorded in `login_attempts`.
- [ ] The browser network tab on the inbox shows no colleague email or phone.
- [ ] The browser network tab on the dashboard shows no `price_monthly` and no
      `page_id`.
- [ ] `EMAIL_BACKEND=smtp` with real credentials delivers a reset link to a
      real mailbox, and the link works.
- [ ] `APP_PUBLIC_URL` matches the real origin, or every reset link points
      nowhere.
- [ ] `CORS_ORIGINS` names the production origin and nothing else.
- [ ] `TZONE_MASTER_KEY` has an offline copy stored separately from the
      backups.
- [ ] A restore from backup was performed and verified, not just taken.

## Patch 9

- [ ] Deferred source is safely applied to the official branch.
- [ ] Git shows only expected changed files.
- [ ] Python compilation passes.
- [ ] Fresh database startup passes.
- [ ] Workflow/API tests pass.
- [ ] Frontend production build passes.
- [ ] Protected Messenger files are verified unchanged or explicitly approved.
- [ ] Rollback is proven.

## Manual workflow

- [ ] Moetaz can acquire and reply.
- [ ] Test cannot take over or reply while Moetaz owns.
- [ ] Stale action returns 409 and refreshes owner.
- [ ] Admin override/transfer/release works.
- [ ] Return to AI works and AI does not race human ownership.
- [ ] Timeline remains visible and complete.
- [ ] Read/unread permissions are correct.
- [ ] Notification bell max five/See all/Clear shown works.
- [ ] Notification state is isolated per employee.
- [ ] Search and counters work.
- [ ] Dark theme is readable.
- [ ] Refresh, background tab, and reconnect preserve correct state.

## Channels

- [ ] Real Messenger inbound message reaches the conversation.
- [ ] AI reply works when allowed.
- [ ] Human reply sends successfully.
- [ ] Handover stops AI.
- [ ] Logs contain no sensitive token values.

## Operations

- [ ] Production database backup exists and restore was tested.
- [ ] `.env`/secrets are present only on the target environment.
- [ ] Super Admin access is confirmed.
- [ ] Monitoring/log review owner is assigned.
- [ ] Rollback decision-maker and procedure are known.
- [ ] Launch smoke-test checklist is assigned.

## Release

- [ ] Owner explicitly accepts Manual QA.
- [ ] Documentation and changelog are updated.
- [ ] Commit and PR checks pass.
- [ ] PR is merged to `main`.
- [ ] Release tag is created.
- [ ] Deployment record is saved.
- [ ] Post-launch monitoring window is completed.

## Current conclusion

The security work listed above is complete and covered by the test suite. What
remains before launch is the manual list — every item on it depends on the real
server, the real domain and a real mailbox, so none of it can be closed from a
development machine — together with manual QA, merge and the deployment record.

The state is therefore **not yet approved for launch**, and the reason is now a
short, specific list rather than an open question.
