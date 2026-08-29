# T-ZONE Platform — Handbook / الدليل الشامل

> **ابدأ من هون.** هذا الملف يشرح كل شيء بالترتيب: كيف المنصة مبنية، كيف تُطوّر عليها،
> كيف يُسجَّل كل شيء، كيف تُنشر على السيرفر، وكيف تشتغل عليها يوماً بيوم. مكتوب ليكون
> واضحاً لأي شخص يقرأه — مالك، مطوّر، أو مساعد ذكي.
>
> **Read this first.** One document, in order: how the platform is built, how you
> develop on it, how everything is recorded, how it is deployed, and how it is
> operated. Written to be clear to anyone who reads it — owner, developer, or AI.

The deep-dive documents (`docs/*.md`) are linked at the end. This handbook is the
map; they are the territory.

---

## 0. ما هي المنصة / What it is

منصة عمليات عملاء متعددة الشركات (multi-tenant): كل شركة لها قاعدة بياناتها **المشفّرة
بمفتاحها الخاص**، ولوحة تحكم مركزية (Super Admin) تدير الشركات **بدون أن تقرأ بياناتها**.
القنوات (Messenger / Instagram / WhatsApp / Telegram) تدخل الرسائل، ومساعد ذكي يرد،
والموظفون يتابعون من لوحة موحّدة.

A multi-tenant customer-operations platform. Every company has its **own
SQLCipher-encrypted database, sealed with its own key**. A central Super Admin
console governs companies **without being able to read their data**. Channels feed
messages in, an AI assistant replies, and staff work from one dashboard.

### القواعد الحاكمة / The governing rules
1. **الكود آلية، مش سياسة.** لا يوجد ملف مشترك يقرّر سلوكاً يراه عميل شركة. كل إعداد
   يغيّر ما يصل لعميل شركة يكون بيانات في قاعدة تلك الشركة.
   *The code is a mechanism, not a policy. No shared file decides behaviour a
   customer sees; each such setting lives in that company's own database.*
2. **الأمان أولاً.** كل بيانات الشركات معزولة بالتشفير وبفصل الملفات. الأسرار مختومة
   ولا تصل المتصفح أبداً. *Security first: per-company encryption, sealed secrets,
   nothing sensitive on the wire.*
3. **لا يتغيّر شكل الواجهة إلا بقرار.** *The visual interface is not changed casually.*

---

## 1. كيف المنصة مبنية / How it is built (architecture)

### الطبقات / The layers

```
Browser (React SPA)  ──HTTPS──►  nginx / reverse proxy  ──►  FastAPI app (uvicorn)
                                                              │
                     ┌────────────────────────────────────────┤
                     ▼                                         ▼
        Control database (shared)                  Tenant databases (one per company)
        companies, users, roles,                   conversations, messages, customers,
        channel_accounts, plans,                   knowledge, catalogue, appointments,
        subscriptions, audit_log                   tasks, team_chat …  (SQLCipher, per-key)
```

- **Frontend** — a React single-page app in `frontend/`, built to `frontend/dist`.
  It talks to the API and never holds secrets. Its design is fixed; treat it as
  such.
- **Backend** — a FastAPI app. Entry point is `main.py`. It:
  - registers every API router (`backend/api/routes/*.py`),
  - installs middleware (security headers, session-cookie/CSRF) and error handlers,
  - runs a startup **lifespan** that upgrades tenant schemas and builds the work
    index,
  - serves the built `frontend/dist` itself **when no static server sits in front**
    (so a plain reverse proxy works), and proxies nothing it does not own.
- **Two database planes** (`database/manager.py`):
  - `database_manager.control()` — the shared control database. Ids are **global**
    here; a control query is safe only when it carries `WHERE company_id = ?`.
  - `database_manager.tenant(company_id)` — one company's encrypted file. Safe by
    **file separation**; a query there cannot reach another company.
- **Services** (`backend/services/*.py`) hold the logic; routes stay thin. Gates
  (`company_gate`, `subscription_gate`, `module_gate`, `stream_access`) decide, per
  company, whether work proceeds.
- **Channels** (`channels/*`) receive webhooks, verify signatures, and hand events
  to the engine (`core/engine.py`) which composes the AI reply.

### أين يعيش كل شيء / Where things live
| Path | What |
|---|---|
| `main.py` | app assembly, middleware, lifespan, SPA serving |
| `backend/api/routes/` | every HTTP endpoint |
| `backend/api/schemas/` | request/response shapes (Pydantic) |
| `backend/services/` | business logic |
| `backend/security/keyring.py` | key derivation, sealing, workspace codes |
| `database/manager.py` | control vs tenant DB access, migrations |
| `database/schema_control.py` / `schema_tenant.py` | table definitions + versions |
| `core/` | the AI engine, router, working hours, memory |
| `channels/` | Meta / WhatsApp / Telegram webhooks and senders |
| `config/settings.py` | every setting the code reads |
| `tools/manage_platform.py` | the operator CLI (create company, backup, check…) |
| `frontend/` | the React interface (built to `frontend/dist`) |
| `deploy/` | installer, systemd unit, nginx, backup cron |
| `tests/` | the automated test suite |

More detail: `docs/ARCHITECTURE.md`, `docs/PROJECT_FILE_MAP.md`,
`docs/CONVERSATION_STATE_MACHINE.md`.

---

## 2. كيف تطوّر عليها / How to develop and change it

You keep full freedom to modify and extend — the layout above is what keeps that
sane. Add logic in a service, expose it through a thin route, describe the shapes
in a schema, and cover it with a test.

### تشغيل محلي / Run it locally
```bash
# once
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt
export TZONE_MASTER_KEY=$(python3 -c "from backend.security.keyring import generate_master_key;print(generate_master_key())")
export DATA_DIR=./data
# create your first company + owner
python3 -m tools.manage_platform create-company --name "Dev Co" --slug dev \
  --workspace "Dev" --owner-email you@example.com --owner-name You --owner-password ChangeMe12345
# build the interface once
cd frontend && npm ci && npx vite build && cd ..
# run
uvicorn main:app --reload --port 8000
# open http://127.0.0.1:8000
```

### إضافة ميزة / Add a feature (the shape to follow)
1. **Schema** — add the request/response model in `backend/api/schemas/`.
2. **Service** — put the logic in a `backend/services/*.py`; use
   `database_manager.tenant(company_id)` for a company's own data,
   `database_manager.control()` (always with `company_id = ?`) for control data.
3. **Route** — a thin handler in `backend/api/routes/`, guarded by
   `Depends(require_permission("..."))`, that calls the service.
4. **Test** — add a test in `tests/` that drives the real path and asserts the
   real outcome (not just a 200).

### قواعد ذهبية / Golden rules while developing
- **Company data is isolated.** Never read another company's tenant file; never run
  a control query without `company_id = ?` unless the caller is a platform admin.
- **A check and the write it guards must be one transaction.** The idiom is
  `conn.execute("BEGIN IMMEDIATE")` then re-check then write (see
  `channel_account_service`, `roles.py`, `customer_service`).
- **Secrets stay sealed.** Anything ending in `_sealed` is encrypted with the
  company/master key; never return it to the browser. `sanitize_user` and the
  channel `_public` shaper are allow-lists — keep them that way.
- **Ids from the URL are attacker input.** Bound them, scope them, and let an
  out-of-range id fall to the shared 404 handler (`backend/api/errors.py`), never
  a 500.
- **Don't change the frontend design.** A rebuild must reproduce the same bundle.
- **Every setting the code reads is documented** in `.env.example` — a test
  (`tests/test_configuration_is_documented.py`) enforces it.

### الاختبارات / Tests — run them before you push
```bash
export TZONE_MASTER_KEY=$(python3 -c "from backend.security.keyring import generate_master_key;print(generate_master_key())")
export DATA_DIR=/tmp/tztest && rm -rf $DATA_DIR
python3 -m pytest tests/ -q          # the whole suite must stay green
python3 -c "import main"             # the app must import
python3 -m tools.manage_platform check   # the platform self-check must pass
cd frontend && npx vite build        # the interface must build
```
The rule the suite lives by: a test that passes against broken code proves
nothing. Assert the real thing. See `docs/TESTING_STRATEGY.md` and `AGENTS.md`.

More detail: `docs/DEVELOPMENT_WORKFLOW.md`, `docs/ONBOARDING.md`.

---

## 3. كيف يُسجَّل كل شيء / How everything is recorded (logging & audit)

There are three separate records, on purpose.

### أ. سجل التدقيق للمالك / The owner's audit trail
Meaningful actions (a role granted, a channel connected, a setting changed, a
customer edited) are written with **who, when, from which IP, and the value before
and after**. A company owner reads their own trail; the platform stores security
events in the control plane too (the "security mirror") so the console can see
sign-ins, failed logins, permission denials, plan changes and webhook-signature
failures — **without** reading the company's content.
- Code: `backend/services/activity_service.py`, `audit_log` (control),
  per-tenant audit tables. Actions are one enum (`Action`), not scattered strings.

### ب. عدّادات الاستهلاك / Usage counters
Numbers, never content: inbound, human-outbound and AI replies, per channel and
per department and per month, in the control plane — enough to bill and to show a
usage screen, and nothing that could leak a conversation.

### ج. سجلّات التشغيل / Runtime logs
Ordinary application logs go to the journal (systemd) and to `LOG_DIR`.
```bash
journalctl -u tzone-api -f        # live application log on the server
```
Failure paths log the company id, the account id and the *type* of an exception —
**never** key material, a sealed value, a token, or a database filename.

More detail: `docs/DATA_AND_SECURITY.md`, and the audit section of
`docs/LAUNCH_READINESS.md`.

---

## 4. كيف تُنشر على السيرفر / How to deploy to a server

### الطريق السريع — أمر واحد / Fast path — one command
On a fresh Ubuntu/Debian VPS, once the code is on the server:
```bash
sudo bash /opt/tzone/deploy/install.sh
```
It does everything — packages, service account, Python env, master key,
environment file, first company, frontend build, service, and (on a bare server)
nginx + HTTPS + backups — then verifies the result. It:
- **detects a control panel** (CloudPanel / Plesk / cPanel) and, when present,
  installs only the app on `127.0.0.1:8000` and leaves nginx and any existing
  WordPress untouched — you publish it as a **reverse-proxy site** in the panel;
- is **safe to re-run** and **never regenerates the master key**.

Getting the code on the server first:
```bash
mkdir -p /opt/tzone
git clone https://github.com/tzonelb/tzone-assistant.git /opt/tzone
cd /opt/tzone && git checkout <branch>
```

### على سيرفر فيه CloudPanel (مثل Hostinger) / On a CloudPanel server
1. Run the installer (it installs the app only).
2. Point a DNS **A record** for your domain at the server IP.
3. Publish the app through CloudPanel — either the UI (Sites → Add Site → Reverse
   Proxy → target `http://127.0.0.1:8000`, then Let's Encrypt SSL) or `clpctl`:
   ```bash
   clpctl site:add:reverse-proxy --domainName=YOUR.DOMAIN \
     --reverseProxyUrl=http://127.0.0.1:8000 --siteUser=app --siteUserPassword='StrongPass1!'
   clpctl lets-encrypt:install:certificate --domainName=YOUR.DOMAIN
   ```
The app serves its own interface, so the reverse proxy needs no extra static
configuration.

### التحديث: ادفع فقط / Redeploy: just push (no server console)
After the installer runs, a systemd timer checks the deployed branch every 3
minutes and deploys new commits itself — pull, rebuild only what changed, restart,
health-check on `/health`, and **roll back automatically if the new version is
unhealthy**. So shipping a change is:
```bash
git push        # to the deployed branch (TZONE_DEPLOY_BRANCH); nothing else
```
The server never needs to be opened for a code change. A schema change applies
itself at startup, and the interface is rebuilt only when `frontend/` changed.
Watch or force it:
```bash
tail -f /var/log/tzone-update.log          # what the auto-updater did
sudo bash /opt/tzone/deploy/update.sh      # deploy right now instead of waiting
```
Only pushes to the **one pinned branch** deploy; any other branch is ignored. All
git/npm/pip work runs as the `tzone` user, so the "dubious ownership" error never
occurs.

**Private repo:** before flipping the GitHub repo to private, run
`sudo bash /opt/tzone/deploy/setup-deploy-key.sh` once — it sets up a read-only
deploy key so `git push` keeps auto-deploying.

**Super Admin:** `install.sh` now creates the platform operator account; there is
no manual step for it on a fresh install (older installs: run `create-super-admin`
once, below).

Full manual walkthrough: `deploy/README.md` and `docs/DEPLOYMENT.md`.

---

## 5. كيف تشتغل عليها / How to operate it (day to day)

### الدخول / Logging in
A user needs three things: the **workspace code** (shown once at company creation),
their **email**, and their **password**. The Super Admin console signs in with
email + password + a mandatory second factor (TOTP).

### مهام المشغّل الشائعة / Common operator tasks (the CLI)
```bash
# run these with the env loaded:  set -a; . /etc/tzone/tzone.env; set +a
python -m tools.manage_platform check                      # health / preflight
python -m tools.manage_platform create-company ...         # a new company (+ owner)
python -m tools.manage_platform rotate-workspace-code --company-id N
python -m tools.manage_platform backup --output /var/backups/tzone
python -m tools.manage_platform unlock-user --email x@y.com # emergency unlock
python -m tools.manage_platform reset-password --email x@y.com
python -m tools.manage_platform create-super-admin ...      # a platform admin
```

### النسخ الاحتياطي والمفتاح / Backups and the master key
- A nightly backup is installed by the installer (`/etc/cron.d/tzone-backup`).
  **Verify a restore, don't just take a backup.**
- The **`TZONE_MASTER_KEY`** unwraps every company database and every backup. It
  lives in `/etc/tzone/tzone.env` on the server; **keep a second copy offline**.
  Lose it and the data is unreadable forever — there is no recovery path.

### الصحة والسجلّات / Health and logs
```bash
systemctl status tzone-api        # is it running
journalctl -u tzone-api -f        # live logs
curl -s https://YOUR.DOMAIN/health/   # should answer {"status":"ok",...}
```

More detail: `docs/ENVIRONMENT_AND_RUNBOOK.md`.

---

## 6. الخريطة الكاملة للوثائق / Full documentation map

| Document | Purpose |
|---|---|
| **`docs/HANDBOOK.md`** (this file) | Start here — everything in order |
| `README.md` | Landing page / quick summary |
| `docs/ARCHITECTURE.md` | System architecture in depth |
| `docs/PROJECT_FILE_MAP.md` | Where important code lives |
| `docs/DATA_AND_SECURITY.md` | Data classes and security expectations |
| `docs/CONVERSATION_STATE_MACHINE.md` | AI ↔ human ↔ ownership contract |
| `docs/DEVELOPMENT_WORKFLOW.md` | Git, patch, test, release |
| `docs/TESTING_STRATEGY.md` | How the tests are designed |
| `docs/ONBOARDING.md` | New developer onboarding |
| `deploy/README.md` | Deployment, step by step + push-to-deploy |
| `docs/FIRST_RUN_CHECKLIST.md` | Prove a live install works for a new user |
| `docs/DEPLOYMENT.md` | Deployment reference |
| `docs/ENVIRONMENT_AND_RUNBOOK.md` | Setup, run, backup, troubleshoot |
| `docs/LAUNCH_READINESS.md` | Production launch checklist (test-backed) |
| `docs/DECISION_LOG.md` | Why the important decisions were made |
| `AGENTS.md` | The engineering discipline every change follows |

---

*Keep this handbook honest: when the build, the logging, the deployment, or the way
you operate changes, change the matching section here first.*
