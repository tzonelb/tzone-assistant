# Offline-first storage and sync

## 1. The rule

**Every write goes to IndexedDB first.** The UI never waits for the network, and never shows an
error because the server is unreachable. The server is a replication target and an audit copy,
not a gatekeeper for day-to-day work.

```
User action
   │
   ├─► local transaction (Dexie): write entity  +  append outbox op   [atomic]
   │
   └─► UI updates from the local DB
                                   … later, when online …
Sync engine ──push──► POST /api/sync/push   (drains outbox)
           ◄──pull──  GET  /api/sync/pull?since=<cursor>
```

## 2. Record envelope

Every syncable record carries:

| Field | Purpose |
|---|---|
| `id` | UUID v4, generated on the creating device — no server round-trip needed |
| `rev` | integer, incremented on every local mutation |
| `updated_at` | ISO-8601 UTC timestamp of the last mutation |
| `deleted` | tombstone flag; rows are never hard-deleted |
| `origin` | device id that last wrote the record |

## 3. Outbox

The outbox is an append-only table of
`{ seq, entity, entityId, op, payload, status, tries, error, queuedAt }`.
It is written in the **same Dexie transaction** as the entity itself, so a crash can never leave
a saved record without its outbox op, or vice versa.

The sync engine drains it in `seq` order, in batches of 200. A batch that the server accepts is
deleted from the outbox. A batch the server rejects with a validation error is marked `failed`
with the server's reason and surfaced in the UI — it is never silently dropped, because a
rejected op means the local ledger and the server ledger disagree.

Retries use exponential backoff (2s, 4s, 8s, … capped at 5 min) and only for transport errors.

## 4. Pull

`GET /api/sync/pull?since=<cursor>&limit=500` returns records whose server sequence is greater
than `cursor`, ordered by sequence, plus the new cursor. The cursor is a monotonic server-side
integer (not a timestamp — clock skew between devices must not be able to skip records).

The client applies pulled records with **last-writer-wins on `updated_at`**, tie-broken by
`origin` device id for determinism. Anything still pending in the outbox for the same record
wins over the pulled version; it will be pushed next cycle.

## 5. Why conflicts are rare here

Accounting documents are append-mostly and immutable after posting:

- A posted journal entry can never be edited — only voided, which is a *new* entry.
- Documents are created on one device and rarely edited on two devices at once.
- The mutable rows (accounts, partners, items, settings) are small and human-edited.

So last-writer-wins is sufficient for the mutable rows, and the immutable rows cannot conflict
by construction. The one case that needs care is **document numbering**, below.

## 6. Offline-safe numbering

Two devices offline at the same time will both want `INV-000042`. Solutions like "ask the server"
break offline use, so numbers are namespaced by device:

```
INV-<device_code>-<counter>       e.g. INV-A7-000042
```

`device_code` is a short, stable code assigned to the device the first time it registers (and
persisted locally). The counter is per-device and per-document-type. The result is globally
unique with no coordination, and it is human-readable — a number tells you which terminal issued
the document.

Sequential, gapless numbering (required by some tax authorities) is a **server-side concern**:
`/api/sync/push` assigns a `legal_no` from a single server counter when a document arrives, and
returns it to the client. Until the document syncs it displays only its device number. This is
documented in the UI, not hidden.

## 7. Service worker

`frontend/public/sw.js` uses:

- **App shell**: cache-first with a versioned cache name. A new deployment changes the version,
  the new worker precaches, then `skipWaiting`.
- **API**: network-only. API responses are never cached — the local database is the offline
  data source, and a cached API response would compete with it as a second source of truth.

## 8. What is deliberately not built

- Operational-transform / CRDT merge of concurrent edits — unnecessary given §5.
- Offline caching of API GETs — see §7.
- Encryption of the local database. IndexedDB is readable by anyone with access to the browser
  profile. Devices holding company books must be OS-encrypted and password-locked; this is an
  operational requirement, and the roadmap tracks an optional passphrase-derived encryption layer.
