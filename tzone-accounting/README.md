# T-ZONE Accounting

> A modular, offline-first double-entry accounting system. A small kernel plus independent
> modules — the Odoo idea — where every capability is a directory you drop in. Works fully
> offline in the browser and syncs when a connection is available. Arabic and English, RTL and LTR.

## العربية — الفكرة باختصار

**T-ZONE Accounting** برنامج محاسبة بنظام القيد المزدوج، مبني على فكرتين أساسيتين:

**١. نواة صغيرة + موديولات مستقلة (مثل فكرة Odoo).**
البرنامج نفسه صغير: النواة تعرف فقط كيف تُحمّل الموديولات، وتُزامن أي جدول يُعرّفه أي موديول،
وتركّب شاشاته وواجهاته. النواة **لا تعرف شيئًا عن المحاسبة** — كل مفهوم محاسبي موجود داخل موديول.

كل موديول مجلد واحد فيه `manifest` يذكر اسمه واعتمادياته (`depends`)، ودالة `setup()` واحدة يسجّل
فيها: جداوله، واجهاته، شاشاته، قوائمه، ترجماته، وقواعد الترحيل المحاسبي. **إضافة ميزة جديدة =
إضافة مجلد**، بدون تعديل أي سطر في النواة أو في الموديولات الأخرى. لذلك يستوعب البرنامج ١٠٠
موديول بنفس السهولة التي يستوعب بها ٩.

**٢. يشتغل أونلاين وأوفلاين.**
كل البيانات محفوظة محليًا في المتصفح (IndexedDB)، فتقدر تفتح البرنامج وتسجّل فواتير وقيود وأنت
بدون إنترنت. كل التقارير تُحسب محليًا. وعند رجوع الاتصال تتم **المزامنة تلقائيًا** في الاتجاهين
عبر صندوق صادر (Outbox) يضمن عدم ضياع أي عملية.

## The module system in one screen

```
KERNEL (knows nothing about accounting)          MODULES (all the accounting is here)
  registry   load, resolve depends, hold           base        identity, settings, shell
  entities   replicate any declared table          accounting  chart of accounts + journal
  sync       push/pull, outbox, conflicts          partners    customers & suppliers
  hooks      the bus modules meet at               catalog     items & services
  money      integer minor units                   documents   pluggable paperwork layer
  i18n       locales merged from modules           invoicing   sales & purchase invoices
  db         schema assembled from modules         payments    receipts & payments
                                                   reports     the financial statements
                                                   dashboard   cards other modules contribute
                                                   audit_log   pure listener, imports nobody
```

Adding a module is one directory and one line in the module list. The kernel resolves load order
from each manifest's `depends`, so you never manage it by hand:

```python
MANIFEST = {
    "name": "Expense claims",
    "depends": ["accounting", "partners"],
    "sequence": 45,
}
```

```python
def setup(registry):
    registry.add_schema(SCHEMA)
    registry.add_entity(EntityDescriptor(name="expense_claim", table="expense_claims", ...))
```

That is the entire integration. The new table now replicates through the sync protocol, appears
in `/api/sync/status`, is picked up by the audit log, and shows in the Modules screen — with
nothing outside that directory edited. Full guide: **[docs/MODULES.md](docs/MODULES.md)**.

Two things prove the modules are genuinely independent, and both are asserted in the test suites:

- `ACCOUNTING_MODULES=reports` boots a server with only `base + accounting + reports`. The
  invoicing endpoints 404, its entity is unknown to sync, and everything else still works.
- `audit_log` logs every entity any module will ever add, because it listens to a generic kernel
  hook rather than to anything accounting-specific.

## Why offline-first

A shop, a warehouse, or a technician in the field cannot stop invoicing because the internet is
down. The browser holds a complete, authoritative copy of the company ledger:

| Capability | Offline | Online |
|---|---|---|
| Create and post invoices, receipts, payments, journal entries | ✅ | ✅ |
| Full financial reports | ✅ computed locally | ✅ |
| Document numbering | ✅ device-scoped | ✅ + gapless official number |
| Multi-device data sharing | queued | ✅ |
| Server-side audit copy | queued | ✅ |

Every write goes to the local database and its outbox in **one transaction**, so a crash can
never leave a saved record without its replication op. The sync engine drains the outbox whenever
the device is online and pulls remote changes in the same cycle. A record the server refuses is
kept, flagged, and shown to the user — never silently dropped.

## Feature set

**Ledger** — hierarchical chart of accounts; manual journal entries with unlimited lines;
draft → posted → void with mirrored reversing entries; integer minor units end to end;
multi-currency with per-document FX rate and base-currency mirroring; period lock date.

**Documents** — sales and purchase invoices with per-line tax; receipts and payments with
allocation against open invoices; automatic balanced journal entry from every posted document;
device-scoped offline numbers plus server-assigned gapless official numbers.

**Reports** (all computed on the device, all available offline) — trial balance, general ledger,
profit & loss, balance sheet, AR/AP aging, partner statement, cash position.

**Platform** — installable PWA with a service worker; Arabic/English with RTL; JWT auth with
per-device registration; a Modules screen showing the live dependency graph and every hook's
listeners.

## Repository layout

```
backend/                FastAPI kernel + modules (Python 3.11+, SQLite/WAL)
  app/core/             registry, entities, sync, hooks, bootstrap
  app/modules/          base · accounting · partners · catalog · documents
                        invoicing · payments · reports · audit_log
  tests/                pytest — kernel, ledger, sync and reports
frontend/               React 19 + Vite + TypeScript PWA
  src/core/             registry, db, repository, sync, hooks, money, i18n
  src/modules/          the same module set, plus dashboard
shared/                 chart of accounts shared by both seeds
docs/                   architecture, accounting model, sync protocol, module guide, roadmap
```

## Quick start

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.seed               # builds the schema from the installed modules and seeds them
uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```

Seeded login: `admin` / `admin123` — change it before any real use.
`GET /api/system/modules` shows what loaded, in what order, and which hooks have listeners.

### Frontend

```bash
cd frontend
npm ci
npm run dev                      # http://127.0.0.1:5173
```

Targets `http://127.0.0.1:8010`; override with `VITE_API_URL`.

**To see the offline path**: load the app once, then stop the backend (or set the browser to
offline). Everything except sync keeps working and the pending counter in the topbar grows.
Restart the backend and the queue drains.

### Tests

```bash
cd backend  && python -m pytest      # 42 tests: kernel, ledger invariants, sync, reports
cd frontend && npm run test          # 48 tests: kernel, money, posting rules, report calculators
cd frontend && npm run build         # type-check + production build
```

## Documentation

1. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — kernel, modules, data flow, trust boundaries
2. [docs/MODULES.md](docs/MODULES.md) — **how to write a module**, hooks, adding a document type
3. [docs/ACCOUNTING_MODEL.md](docs/ACCOUNTING_MODEL.md) — entities, posting rules, report definitions
4. [docs/OFFLINE_SYNC.md](docs/OFFLINE_SYNC.md) — sync protocol, conflict policy, numbering
5. [docs/ROADMAP.md](docs/ROADMAP.md) — what is built and which modules come next

## Ownership

T-ZONE business software. Repository access does not grant permission to reuse the business
logic, branding, or data. Never commit `.env`, tokens, or runtime databases.
