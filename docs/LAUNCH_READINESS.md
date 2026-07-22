# Launch Readiness Checklist

The system is launch-ready only when every P0 item is complete.

## Source and security

- [ ] Final candidate is on the approved branch and exact commit.
- [ ] Working tree is clean.
- [ ] No secrets or forbidden runtime files are tracked.
- [ ] Production tokens were rotated after any prior exposure.
- [ ] Branch protection, Secret Protection, and Push Protection are active.

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

As of 2026-07-22, Patch 9 installation, frontend build, manual QA, merge, and deployment checks remain. Therefore the current state is not yet approved for launch.
