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
- [x] A branch id belonging to another company is refused at the write, and a
      row already holding one displays nothing. `test_branch_ownership.py`
- [x] A scheduled post publishes through the page the company chose, and an
      account belonging to another company cannot fetch its token.
      `test_scheduled_post_account.py`
- [x] A ticket cannot be assigned to somebody who does not work here, checked
      by walking the source so a new endpoint cannot forget. `test_tasks.py`
- [x] A sign-in stopped only by the workspace code — meaning the password was
      correct — reaches the owner's log, while the caller still gets the same
      401 as any other failure. `test_security_events.py`
- [x] A refused webhook is recorded, and a flood of them writes one entry a
      minute per source rather than one per attempt.
- [x] A refused action reaches the owner's log, throttled per employee and
      permission so hammering a 403 cannot bury the log in its own entries.
- [x] Every `Action` the platform declares is raised somewhere. An event named
      and never written makes the owner's log lie by omission.
- [x] Every permission the Roles screen offers is enforced by some endpoint,
      and every permission an endpoint requires is one a company can hold.
      `test_permissions_are_enforced.py`
- [x] Every setting a company can store is either read by something or
      declared unimplemented, and no screen offers an unimplemented one.
      `test_settings_are_implemented.py`
- [x] The channels a screen offers are the channels the platform can connect,
      in every place one is named. `test_channel_catalogue.py`
- [x] The settings catalogue that is seeded is the one that is served, so no
      key can be stored where nothing can read or write it.
- [x] A company's opening hours decide what a customer is told when the team
      is not there, in the company's own timezone, failing open on anything
      unreadable. `test_working_hours.py`
- [x] A company's chosen reply language is used, and a customer who asks to
      switch still wins. `test_reply_language.py`
- [x] Each kind of bell entry is written only if the company asked for it, and
      the operator's module switch still overrules.
      `test_notification_preferences.py`
- [x] A company can create, rename, retire and delete its own branches, and
      cannot touch another company's. `test_branches.py`
- [x] No table is read by something and impossible to insert into, and none is
      written by something and readable by nothing. Both registries are empty.
      `test_no_table_is_write_only.py`
- [x] The old value of a setting and of a customer field can be read back, each
      behind the permission that guards the thing it describes, and both are
      pruned with the log entry they belong to. `test_audit_detail.py`
- [x] Every knob the platform reads is documented, every documented knob is
      read, and every import is installable.
      `test_configuration_is_documented.py`
- [x] No file shipped under `config/`, `features/` or `data/` is loaded by
      nothing. `test_no_shared_file_is_orphaned.py`

## Security — closed by the adversarial audit

Ten defects an agent-driven penetration pass surfaced, each reproduced before it
was fixed and held afterward by the named test.

- [x] Inbound routing matches only a channel's guarded identifier, so a company
      cannot set an Instagram account's free-form `page_id` to a rival's id and
      intercept its DMs (or silence its Telegram inbox).
      `test_a_channel_routes_only_on_its_guarded_id.py`
- [x] A dashboard route checks its permission against the company whose data it
      returns, not the session's active company, so a multi-company member
      cannot read a company where their role withholds it.
      `test_a_dashboard_only_answers_for_a_company_you_may_see.py`
- [x] A customer's message cannot become a live formula in the conversation CSV
      export. `test_an_export_cannot_smuggle_a_spreadsheet_formula.py`
- [x] Request lists that expand into `IN (?, ...)` are length-bounded, so an
      oversized body is refused, not crashed on.
      `test_a_request_list_cannot_grow_without_bound.py`
- [x] A TOTP code is single-use: a validated step cannot be replayed within its
      window. `test_two_factor.py`
- [x] Message dedup is scoped to the conversation, so two Telegram customers
      sharing a per-chat message id are both kept.
      `test_two_customers_are_not_one_message.py`
- [x] A delegated user-manager cannot grant a permission they do not hold, nor
      assign the Owner role; only a caller with no ceiling can.
      `test_a_delegated_admin_cannot_climb_above_their_role.py`
- [x] A forged `X-Forwarded-For` that is not a valid IP is not written to the
      throttle key or the audit log.
      `test_a_forged_forwarded_header_is_not_believed.py`
- [x] Concurrent assistant previews are capped, so a preview flood cannot hold
      every worker thread and freeze the platform.
      `test_a_preview_flood_cannot_starve_the_platform.py`
- [x] The password KDF runs at 600k iterations for newly set passwords, matching
      current OWASP guidance; existing hashes verify against their stored count.

Deferred, lower severity (documented, not yet fixed):

- [ ] `channel_accounts.*_sealed` columns bind their AEAD to (company, field)
      but not to the row. An attacker with **write access to the control
      database** could copy one sealed credential onto another of the *same
      company's* rows; the value still cannot be decrypted or moved across
      companies. Closing it means adding the row id to the seal's AAD and
      re-sealing existing values in a migration; held back only to avoid a risky
      re-seal across every company's credentials without a maintenance window.

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

The security work listed above is complete and covered by the test suite — over 900
tests at the time of writing. What remains before launch is the manual list —
every item on it depends on the real server, the real domain and a real mailbox,
so none of it can be closed from a development machine — together with manual
QA, merge and the deployment record.

One gap is known and open rather than closed, recorded here so it is not
discovered as a surprise.

**Branches have no screen.** They can now be created, renamed, retired and
deleted through the API and the CLI, and the two screens that already read the
list — the branch selector on every team member, the branch field on every
channel — fill in as soon as one exists. What is missing is a form for managing
them, which is a design change and therefore the product owner's decision
rather than one to take while closing defects.

That is the only one. The second gap this section used to record —
`company_setting_audit` and `customer_audit` written and never read — is
closed. `/api/activity/settings/{section}/history` and
`/api/activity/customers/{id}/history` read them, each behind the permission
that guards the thing it describes rather than behind the log's own, because
the values are the sensitive half; and both tables are now pruned on the same
clock as the log entry they belong to.

The state is therefore **not yet approved for launch**, and the reason is now a
short, specific list rather than an open question.
