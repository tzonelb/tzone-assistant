# Contributing to T-ZONE

## Branch model

- `main`: accepted, protected state.
- `develop`: optional future integration branch.
- `patch/*`: corrective work.
- `feature/*`: new modules.
- `docs/*`: documentation-only work.

## Before work

```powershell
cd C:\PROJECTS\tzone-assistant
git status
git branch --show-current
git rev-parse HEAD
git log -5 --oneline --decorate
```

The working tree must be clean. Create a scoped branch from the approved base.

## Commit discipline

- Stage explicit paths; avoid `git add .` unless the status was fully reviewed.
- One commit should represent one coherent change.
- Use messages such as:
  - `fix: prevent stale conversation takeover`
  - `test: cover two-agent ownership conflict`
  - `docs: update Patch 9 acceptance status`
- Do not mix unrelated refactors with release-blocker fixes.

## Pull Request requirements

Every PR must include:

- exact base commit;
- problem statement;
- scope and non-scope;
- files changed;
- database/migration impact;
- automated test results;
- manual QA result;
- screenshots for UI changes;
- rollback plan;
- documentation updates.

Use `.github/PULL_REQUEST_TEMPLATE.md`.

## Required checks

At minimum:

- Python compilation
- Backend startup against a fresh database
- API and workflow regression tests
- Frontend production build
- Secret/forbidden-file scan
- Multi-user ownership test for conversation changes
- Manual QA for user-facing workflow changes

## Review rule

A green build does not prove the workflow is correct. User acceptance is a separate gate.
