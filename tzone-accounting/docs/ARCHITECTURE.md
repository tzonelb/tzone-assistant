# Architecture

Two ideas hold this system up:

1. **A small kernel and many modules.** The kernel loads modules, replicates any table they
   declare, and mounts their screens and endpoints. It contains no accounting concepts.
2. **The browser owns the working copy of the ledger.** Every write lands in IndexedDB first;
   the server is a replication target and an audit copy, not a gatekeeper.

Neither is negotiable in the other's favour: the module system is what makes a hundred features
possible, and the local-first design is what makes the program usable in a shop with no internet.

## Components

```
┌───────────────────────── Browser (installable PWA) ──────────────────────────┐
│  KERNEL  src/core/                                                           │
│    registry   resolve the module graph, hold what modules contributed        │
│    db         Dexie schema assembled from module-declared tables             │
│    repository the only writer: entity + outbox in ONE transaction            │
│    sync       drains the outbox, pulls changes, resolves conflicts           │
│    hooks      the extension bus modules meet at                              │
│    money      integer minor-unit arithmetic                                  │
│    i18n       locales merged from modules, RTL/LTR                           │
│                                                                              │
│  MODULES src/modules/                                                        │
│    base · dashboard · accounting · partners · catalog                        │
│    documents · invoicing · payments · reports                                │
│      each: manifest + setup(ctx) + its own screens, rules and tests          │
└──────────────┬───────────────────────────────────────────────────────────────┘
               │ HTTPS, JWT
┌──────────────┴───────────────────── FastAPI ─────────────────────────────────┐
│  KERNEL  app/core/                                                           │
│    registry   discover manifests → resolve order → run setup(registry)       │
│    entities   generic replicated-table machinery (any module's table)        │
│    sync       /api/sync/push and /api/sync/pull, entity-agnostic             │
│    bootstrap  schema built from module fragments, then module seeds          │
│    hooks      record_stored, sync_pushed, document_types, …                  │
│                                                                              │
│  MODULES app/modules/                                                        │
│    base · accounting · partners · catalog · documents                        │
│    invoicing · payments · reports · audit_log                                │
└──────────────────────────────────────────────────────────────────────────────┘
```

The two module lists are deliberately near-identical. A capability that has both a data model and
a screen is one directory on each side, with the same key, the same `depends`, and the same
entity names — the entity name is what the two halves agree on across the wire.

## Loading, in three passes

Both kernels do the same thing:

1. **Discover** — read every manifest. Manifests are pure data, so the dependency graph is known
   before any module code is imported.
2. **Resolve** — topological sort over `depends`, ties broken by `sequence` then key. The order
   is identical on every machine; a cycle or a missing dependency is reported by name.
3. **Load** — call each module's `setup()`, the one place it registers tables, entities, routers,
   screens, menus, translations, seeds and hooks.

## Data flow

**Write**: screen → repository → one Dexie transaction (record + outbox op) → UI re-renders from
Dexie → the sync engine pushes when online → the server re-validates → stores and bumps
`change_seq`.

**Read**: screen → repository / report calculator → Dexie → render. The server is not in the read
path at all. That is precisely why every screen works offline.

## Why the ledger engine lives on the client

The alternative — server computes, client displays — cannot work offline, and a "read-only when
offline" accounting program is not useful to a shop with a bad connection. So posting rules and
report calculators are pure functions in the client modules, run on the device.

The cost is that the server cannot trust the client's arithmetic. It is paid by re-validating the
*invariants* (balanced, one-sided lines, postable accounts, unlocked period) on push, rather than
re-deriving the document→journal mapping. One implementation of the business rules; two
independent checks that the ledger is sound. See
[ACCOUNTING_MODEL.md §5](ACCOUNTING_MODEL.md#5-validation-split).

## Trust boundaries

| Boundary | Control |
|---|---|
| Browser → API | JWT bearer, short TTL; every sync request carries its `device_id` |
| Push payload | Full re-validation per entity descriptor; a bad op is rejected and *reported*, never silently dropped |
| Period locking | Enforced on both sides; the server value wins and is pulled to clients |
| Document numbering | Device-scoped offline; the gapless `legal_no` is server-assigned |
| Local database | Not encrypted — see [OFFLINE_SYNC.md §8](OFFLINE_SYNC.md#8-what-is-deliberately-not-built) |

## Persistence choices

**SQLite with WAL** on the server: one file, no service to operate, safe concurrent readers with
one writer — right for a single-company deployment, and it matches the stack already used
elsewhere at T-ZONE. The change feed is a monotonic `change_seq` allocated in the same
transaction as the write, which is what makes `pull` correct without depending on wall-clock time.

**IndexedDB via Dexie** in the browser: the only browser store with transactions and indexes big
enough for a full ledger. Its transaction API is what allows the atomic record+outbox write that
the whole offline design rests on.

## Extending

See [MODULES.md](MODULES.md). In short: a new capability is a directory; a new kind of paperwork
is a `DocumentType` declaration; a new report is a pure function; a reaction to something else is
a hook handler. None of them require editing the kernel.
