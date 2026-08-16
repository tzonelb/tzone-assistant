# Writing a module

The system is a small kernel plus modules. The kernel knows how to load modules, replicate any
table they declare, and mount their screens and endpoints. It contains no accounting concepts at
all — delete every module directory and you are left with a working, empty application.

This document is how you add the tenth module, or the hundredth.

## 1. The shape of a module

Every module is one directory on each side, and both sides look the same:

```
backend/app/modules/<key>/          frontend/src/modules/<key>/
  __manifest__.py   ← metadata        index.ts(x)   ← manifest + setup()
  module.py         ← setup(registry)  …            ← its own screens, rules, tests
  schema.sql        ← its tables
  api.py            ← its endpoints
```

A manifest is **data**: name, version, category, and `depends`. The kernel reads every manifest
before importing a single line of module code, which is how it can resolve the dependency graph
and report a cycle or a missing dependency by name instead of failing halfway through a boot.

`setup()` is the **only** place a module registers anything. Nothing is auto-discovered by naming
convention, so what a module does is readable in one function.

## 2. Adding a backend module

`backend/app/modules/expenses/__manifest__.py`:

```python
MANIFEST = {
    "name": "Expense claims",
    "name_ar": "طلبات المصروفات",
    "version": "1.0.0",
    "summary": "Employee expense claims that post to the ledger once approved.",
    "category": "Accounting",
    "depends": ["accounting", "partners"],
    "sequence": 45,
}
```

`backend/app/modules/expenses/module.py`:

```python
from ...core.entities import EntityDescriptor
from ...core.registry import Registry

SCHEMA = """
CREATE TABLE IF NOT EXISTS expense_claims (
    id         TEXT PRIMARY KEY,
    employee   TEXT NOT NULL,
    amount     INTEGER NOT NULL DEFAULT 0,
    status     TEXT NOT NULL,
    rev        INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    deleted    INTEGER NOT NULL DEFAULT 0,
    origin     TEXT NOT NULL DEFAULT '',
    change_seq INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_claims_seq ON expense_claims(change_seq);
"""


def setup(registry: Registry) -> None:
    registry.add_schema(SCHEMA)
    registry.add_entity(
        EntityDescriptor(
            name="expense_claim",
            table="expense_claims",
            columns=("employee", "amount", "status"),
            required=("employee", "status"),
            defaults={"amount": 0},
        )
    )
```

That is the whole integration. The new table now replicates through `/api/sync/push` and
`/api/sync/pull`, appears in `/api/sync/status`, is logged by the audit-log module, and shows up
in `/api/system/modules` — none of which required editing anything outside this directory.

**The replication envelope is required.** Any table you want synced must carry
`rev, updated_at, deleted, origin, change_seq` and have an index on `change_seq`.

## 3. Adding a frontend module

`frontend/src/modules/expenses/index.tsx`:

```tsx
export const expensesModule: ModuleManifest = {
  key: "expenses",
  name: "Expense claims",
  nameAr: "طلبات المصروفات",
  version: "1.0.0",
  summary: "Employee expense claims.",
  category: "Accounting",
  depends: ["accounting", "partners"],
  sequence: 45,

  setup(ctx) {
    ctx.addEntity({ name: "expense_claim", store: "expense_claims", indexes: "id, status" });
    ctx.addRoute({ path: "/expenses", element: ExpensesPage });
    ctx.addMenu({ path: "/expenses", labelKey: "expenses.title", icon: "🧾", section: "sales" });
    ctx.addTranslations({ en: { "expenses.title": "Expenses" }, ar: { "expenses.title": "المصروفات" } });
  },
};
```

Then add it to the list in `frontend/src/modules/index.ts`. That list is the only place the
application names its modules; load order is still resolved from `depends`.

The Dexie schema is rebuilt from the installed modules on the next boot, so the new table appears
without a migration you have to write.

## 4. What `setup()` can register

| Backend (`Registry`) | Frontend (`ModuleContext`) | Effect |
|---|---|---|
| `add_schema(sql)` | — | Tables, idempotent `CREATE TABLE IF NOT EXISTS` |
| `add_entity(descriptor)` | `addEntity(def)` | A replicated table — joins the sync protocol |
| — | `addLocalStore(name, idx)` | A device-only table, never replicated |
| `add_router(router)` | `addRoute(route)` | Endpoints / screens |
| — | `addMenu(item)` | A sidebar entry |
| — | `addTranslations(bundle)` | Strings, merged into both locales |
| `add_seed(fn)` | `addSeed(fn)` | Data a fresh install needs; must be idempotent |
| `add_settings_defaults(d)` | `addSettingsDefaults(d)` | Keys on the company settings document |
| — | `addDashboardCard(card)` | A card on the front page |
| — | `addSettingsPanel(panel)` | A section on the settings screen |
| `extend_entity(name, …)` | — | Attach validation or write hooks to another module's table |
| `on(hook, fn, seq)` | `on(hook, fn, seq)` | Listen to an extension point |

## 5. Hooks — how modules extend each other

Modules never import each other's internals. They meet at named hooks, and handlers run in
ascending `sequence`, so ordering is explicit without either side knowing the other exists.

| Hook | Where | Purpose |
|---|---|---|
| `document_types` | both | Contribute a document type (see §6) |
| `record_stored` | backend | Fires for every replicated record — the audit log uses this |
| `sync_pushed` | backend | Fires once per push batch, with accepted and rejected ops |
| `entry_posted` / `entry_voided` | frontend | A journal entry hit the ledger |
| `document_posted` | frontend | A source document was posted |

`app/modules/audit_log/` is the smallest complete example: it adds one table, subscribes to two
hooks, exposes one endpoint, and imports no other module. Delete the directory and the feature
disappears cleanly.

## 6. Adding a new kind of document

Document types are the most common thing to add — credit notes, quotations, POS tickets, payroll
runs. You do not add a table; you declare a type.

**Backend** (`app/modules/credit_notes/module.py`):

```python
from ..documents.types import DocumentType

CREDIT_NOTE = DocumentType(
    key="credit_note", prefix="CN",
    label_en="Credit note", label_ar="إشعار دائن",
    module="credit_notes", settles="receivable", role="settlement",
)

def setup(registry):
    registry.on("document_types", lambda: CREDIT_NOTE)
```

**Frontend** — the same declaration plus a `buildEntry` posting rule:

```ts
export const CREDIT_NOTE: DocumentTypeDef = {
  key: "credit_note", prefix: "CN", labelKey: "creditNotes.title",
  module: "credit_notes", settles: "receivable", role: "settlement",
  buildEntry: (document, context) => ({ /* debit/credit lines */ }),
};
```

The type now stores, replicates, gets a gapless server-assigned `legal_no`, gets an offline
device-scoped number, and — because it declared `settles` and `role` — participates in the
receivables aging report. `documents.doc_type` deliberately has **no** `CHECK` constraint, so
none of this needs a schema migration.

## 7. Installing a subset

Both sides accept a module subset, with dependencies pulled in automatically:

```bash
ACCOUNTING_MODULES=reports uvicorn app.main:app     # base + accounting + reports only
```

```ts
installModules(ALL_MODULES, ["reports"]);
```

This is what "the modules are actually independent" means in practice, and it is asserted in
`backend/tests/test_modules.py` and `frontend/src/core/registry.test.ts`: with `invoicing`
uninstalled, its endpoint 404s, its entity is unknown to sync, and its screens do not exist —
while everything else still works.

## 8. Rules that keep a hundred modules workable

1. **Never import another module's internals.** Depend on it in the manifest and meet it at a
   hook or at a registry entry. The one sanctioned exception is importing a *type* declaration
   from a module you already depend on (`documents.types`).
2. **Own your data.** One entity has exactly one owning module; the kernel rejects a second
   claim by name.
3. **Seeds must be idempotent.** They run on every boot, including after a module is installed
   later.
4. **Money is an integer.** Minor units on both sides, always. Never `REAL`, never `float`.
5. **Posting rules are pure functions.** No I/O, no React, no database — everything they need is
   passed in as `PostingContext`. That is what makes them unit-testable and what lets them run
   offline.
6. **Add tests in your module's directory.** `pytest` and `vitest` pick them up automatically.
