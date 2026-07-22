# Developer Onboarding

## Before access

The developer should receive:

- repository access;
- role and responsibility;
- current approved branch/commit;
- non-secret `.env.example`;
- development test accounts;
- test channel identifiers when required;
- this documentation set;
- current checkpoint and acceptance specification.

Never send production tokens through chat or commit them to Git.

## First day

1. Read `README.md`, `PROJECT_MASTER.md`, and `AGENTS.md`.
2. Read current project status, architecture, Patch 9 status, and state machine.
3. Clone repository.
4. Confirm branch and commit.
5. Create local `.env` from `.env.example`.
6. Install backend/frontend dependencies.
7. Run backend, frontend, health endpoint, and login.
8. Review the database schema and key routes.
9. Reproduce the current conversation workflow without modifying code.
10. Document questions and discrepancies.

## First week

- Reproduce automated tests.
- Run the frontend production build.
- Perform manual multi-user QA.
- Review protected Messenger integration.
- Review tenant isolation and security gaps.
- Propose changes through a scoped issue/PR, not through untracked local edits.

## Developer expectations

- Communicate uncertainty.
- Do not claim a test was run when it was not.
- Preserve user-facing behavior unless the scope changes it.
- Prefer root-cause fixes over hotfix chains.
- Write rollback and migration notes.
- Update documentation as part of the change.

## Handover checklist

Before leaving or pausing work, provide:

- branch and commit;
- clean/dirty status;
- files changed;
- tests passed/failed;
- database changes;
- known risks;
- next exact command/action;
- updated checkpoint docs.
