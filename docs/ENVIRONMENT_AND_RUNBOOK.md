# Environment and Runbook

## Supported local environment

Current primary development environment is Windows PowerShell.

Prerequisites:

- Git
- Python compatible with current dependencies
- Node.js/npm compatible with Vite 8
- Local access to configured Meta/WhatsApp test integrations when channel testing is required

## Environment variables

Use `.env.example` as the variable-name reference. The active configuration also supports application/auth variables such as:

- `APP_ENV`
- `DEBUG`
- `DEFAULT_LANGUAGE`
- `COMPANY_NAME`
- `DATABASE_PATH`
- `DATABASE_URL`
- `JWT_SECRET`
- `JWT_ALGORITHM`
- `ACCESS_TOKEN_EXPIRE_MINUTES`
- default workspace/company/branch IDs
- channel tokens and API versions
- OpenAI settings
- upload/media paths

Never add real values to documentation or Git.

## Backend setup

```powershell
cd C:\PROJECTS\tzone-assistant
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Checks:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/docs`

## Frontend setup

```powershell
cd C:\PROJECTS\tzone-assistant\frontend
npm ci
npm run dev
```

Production build:

```powershell
npm run build
```

## Database

Default local path: `database/tzone.db`.

Before high-risk work:

- stop writers when practical;
- copy the database, WAL, and SHM consistently or use a SQLite backup method;
- record timestamp and commit;
- test restore on a separate path.

Never commit database files.

## Common diagnostics

### Backend cannot start

- confirm virtual environment;
- reinstall requirements;
- check `.env` syntax;
- inspect database permissions;
- run Python compilation;
- use root `main.py`, not legacy `backend/main.py`.

### Frontend cannot connect

- ensure FastAPI is on port 8000;
- confirm `VITE_API_BASE_URL` if customized;
- inspect browser network/auth token;
- run `npm ci` after dependency changes.

### Channel failure

- do not rewrite webhook immediately;
- verify environment tokens/API versions;
- inspect normalized event logs;
- confirm outbound gateway result;
- test with non-production credentials where possible.

## Emergency rollback principle

Rollback must restore:

- source commit;
- database backup/migration state;
- environment/configuration reference;
- service version;
- health and channel checks.

A source rollback without database compatibility is not a complete rollback.
