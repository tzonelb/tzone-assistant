# Checkpoint System

A checkpoint is a reproducible project state and decision record. It prevents work from depending on memory, old archives, or incomplete files.

## Checkpoint 0 — Discovery and scope lock

### Do

- Define the business problem and acceptance criteria.
- List scope and explicit non-scope.
- Identify protected behavior and high-risk files.
- Record current branch, commit, status, and environment.
- Review current architecture and database state.

### Produce

- `PROJECT_STATUS.md`
- acceptance specification
- risk list
- file inspection list
- test plan

### Result

Everyone agrees what will change and what must remain untouched.

## Checkpoint 1 — Secure baseline

### Do

- Ensure clean Git state.
- Remove secrets/runtime data from tracking.
- Create `.gitignore`, `.env.example`, and secret protection.
- Create exact baseline commit and tag.
- Back up old history privately when required.

### Produce

- baseline commit SHA
- tag
- clean `git status`
- secret scan report
- backup location

### Result

A safe, reproducible starting point exists.

### Current result

Completed at `b7212114854d5c6f84fea31d1bf5ca912348694c`.

## Checkpoint 2 — Implementation complete

### Do

- Implement the approved scope on a branch.
- Update backend/frontend/database/tests together.
- Avoid unrelated refactors.
- Protect working integrations.
- Document implementation decisions.

### Produce

- source commit or exact patch artifact
- changed-file list
- migration notes
- implementation notes
- automated tests

### Result

Code is complete but not yet accepted.

### Current Patch 9 result

A deferred code-complete artifact exists at `0554e322ad2565e00f116f848a1af52b381a6149`. It is not installed or merged.

## Checkpoint 3 — Automated verification

### Do

- Apply code to the exact branch.
- Run Python compilation/imports.
- Start backend on fresh/test database.
- Run API, state-machine, race, and migration tests.
- Run frontend production build.
- Scan secrets and forbidden files.
- Verify protected files.
- Prove rollback.

### Produce

- machine-readable test result
- build logs
- database migration result
- rollback result
- failure report if any

### Result

The candidate is technically coherent and reproducible.

### Current Patch 9 result

Partially complete on the external artifact. Frontend build and full installed-tree verification remain.

## Checkpoint 4 — Manual acceptance

### Do

Use separate sessions/accounts:

- Employee A: Moetaz
- Employee B: Test
- Administrator

Test ownership, permissions, reply, Timeline, read/unread, notifications, release, transfer, Return-to-AI, refresh, reconnect, dark theme, and real channel traffic.

### Produce

- signed/manual QA checklist
- screenshots/video when useful
- discovered defects
- owner acceptance statement

### Result

The workflow is proven from the user's perspective.

### Current Patch 9 result

Not completed.

## Checkpoint 5 — Release candidate and merge

### Do

- Fix all defects.
- Rerun all tests from zero.
- Update documentation and changelog.
- Commit and push.
- Open Pull Request.
- Pass required checks.
- Merge to `main`.
- Create release tag.

### Produce

- accepted commit
- PR link/number
- merge commit
- release tag
- release notes

### Result

The accepted state becomes the official source of truth.

### Current Patch 9 result

Not completed.

## Checkpoint 6 — Deployment and smoke test

### Do

- Back up production data/configuration.
- Deploy accepted release.
- Run database migrations.
- Start services.
- Verify health, login, conversation, notification, AI/human handover, and channel delivery.
- Monitor logs and metrics.
- Roll back on critical failure.

### Produce

- deployment record
- smoke-test result
- backup and rollback references
- incident report if needed

### Result

The release is live and operational.

## Checkpoint 7 — Post-release review

### Do

- Monitor errors and performance.
- Review user feedback.
- Record incidents and lessons.
- Convert every defect into a regression test.
- Update roadmap/status/docs.
- Lock the next scope.

### Result

The project learns from the release and starts the next phase from a reliable checkpoint.

## Mandatory checkpoint package

```text
CHECKPOINT_YYYY-MM-DD/
├── MASTER_CHECKPOINT.md
├── PROJECT_STATUS.md
├── ARCHITECTURE.md
├── ROADMAP.md
├── CHANGELOG.md
├── ACCEPTANCE_SPEC.md
├── TEST_REPORT.md
├── MANUAL_QA.md
├── DECISION_LOG.md
├── GIT_STATE.txt
├── DATABASE_STATE.md
└── MANIFEST.json
```
