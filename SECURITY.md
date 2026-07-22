# Security Policy

## Never commit

- `.env` or populated environment files
- API keys, access tokens, verify tokens, passwords, private keys
- runtime SQLite databases, WAL, or SHM files
- customer conversation exports
- customer uploads or private attachments
- backups or Git bundles
- `.venv`, `node_modules`, `dist`, logs, or temporary files

## Secret handling

- Store local secrets only in `.env`.
- Keep `.env.example` limited to variable names and safe placeholders.
- Rotate any secret that was ever exposed, even if removed from Git history.
- Keep GitHub Secret Protection and Push Protection enabled.
- Production secrets should move to a managed secret store before launch.

## Tenant isolation

Every tenant-scoped query must include the authenticated workspace/company boundary. Add automated negative tests proving one company cannot read or mutate another company's data.

## Authentication and authorization

- Authorization must be enforced on the backend.
- Frontend hiding is not security.
- Sensitive state changes require audit records.
- Use short-lived access tokens and rotate JWT secrets before production.

## Vulnerability reporting

Do not open public issues containing secrets or customer information. Report sensitive findings privately to the repository owner and include reproduction steps without real credentials.
