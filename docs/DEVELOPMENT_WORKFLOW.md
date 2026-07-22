# Development Workflow

## Branch rules

- `main` is protected and accepted-only.
- Work happens on `patch/*`, `feature/*`, or `docs/*` branches.
- No force push to protected branches.
- Merge through Pull Request.

## Start work

```powershell
cd C:\PROJECTS\tzone-assistant
git fetch --prune
git status
git branch --show-current
git rev-parse HEAD
git log -5 --oneline --decorate
```

Do not continue if the working tree is unexpectedly dirty.

## Scope contract

Before coding, document:

- objective;
- acceptance criteria;
- non-scope;
- protected behavior;
- data/migration impact;
- files to inspect;
- tests to add.

## Implementation

- Read complete current files.
- Make atomic domain changes.
- Keep backend authoritative.
- Add migrations/schema handling.
- Update frontend and tests.
- Preserve channel integrations.
- Do not redesign unrelated UI.

## Verification

```text
Clean baseline
-> Implementation
-> Python compile
-> Fresh DB startup
-> Automated tests
-> Frontend build
-> Secret/forbidden-file scan
-> Rollback proof
-> Manual QA
-> Acceptance
-> Commit/PR/Merge/Tag
```

## Failure policy

On failure:

- stop;
- do not merge;
- do not call it final;
- save logs;
- restore/rollback when required;
- fix in the same cumulative branch;
- rerun the complete suite.

## Patch package contract

When offline patch delivery is unavoidable:

```text
PATCH_NAME/
├── README.md
├── ACCEPTANCE_SPEC.md
├── CHANGELOG.md
├── BASE_COMMIT.txt
├── MANIFEST.json
├── BACKUP.py
├── INSTALL.py
├── VERIFY.py
├── ROLLBACK.py
├── MANUAL_QA.md
├── tests/
└── payload/
```

The installer must fail closed on mismatch and never perform blind merges or overwrite unknown newer files.

## Release naming

- `RC1`, `RC2`, ... before acceptance.
- `ACCEPTED` only after manual acceptance and merge.
- Do not use `FINAL` as a substitute for evidence.
