# Load / Stress Test Report — inbound + outbound at scale

**Date:** 2026-08-10
**Goal:** understand how the platform behaves under ~10,000 messages "at the same
moment" across ~100 companies, sending **and** receiving simultaneously — and fix
what breaks.

**Method:** a harness (`scratchpad/loadtest*.py`) drives the REAL service-layer
write path per message — save message → upsert customer → record conversation
state → create notification — with only the AI/outbound *network* calls stubbed,
across 100 seeded companies, on a thread pool sweeping 1→200 concurrent workers.
It measures throughput, latency percentiles, and error counts (especially SQLite
"database is locked").

---

## TL;DR

- **The bottleneck was NOT the database engine's write speed — it was connection
  setup.** Opening a fresh SQLite connection costs ~5 ms here, and one inbound
  message opened **8–9 connections**, so a single message cost **~100 ms of pure
  connection overhead before any real work or contention.**
- **Fixed** with thread-local connection pooling (+ WAL tuning + removing debug
  `print()`s from the hot path). **Per-message cost dropped ~100 ms → ~5 ms (20×).**
  Validated: **669 backend tests pass**, zero regressions.
- **Measured ceiling after the fix:** **~200 messages/second**, essentially flat
  from 1 to 200 concurrent workers, with **zero lock errors** — for inbound,
  outbound, and 50/50 mixed send+receive alike. This is the **SQLite single-writer
  wall**: only one transaction commits at a time, so adding concurrency raises
  latency (queuing) but not throughput.
- **10,000 messages/second is ~50× beyond that wall.** No amount of SQLite tuning
  changes it — SQLite has exactly one writer. **Sustained 10k/s requires a
  concurrent-writer database (PostgreSQL).** A one-time *burst* of ~10k messages
  can instead be absorbed with a fast-ack write queue (see Options).

---

## Numbers

### Before → after the connection-pooling fix (single worker, no contention)

| | throughput | p50 latency |
|---|---|---|
| Before | ~9 msg/s | ~100 ms/msg |
| After  | ~185 msg/s | ~5 ms/msg |

### Concurrency sweep after the fix (3,000 ops each, inbound)

| workers | throughput | p50 | p95 | p99 | errors |
|---|---|---|---|---|---|
| 1   | 172/s | 5 ms   | 8 ms    | 18 ms   | 0 |
| 10  | 165/s | 21 ms  | 214 ms  | 567 ms  | 0 |
| 50  | 197/s | 31 ms  | 1320 ms | 3143 ms | 0 |
| 100 | 166/s | 111 ms | 2747 ms | 4615 ms | 0 |
| 200 | 160/s | 241 ms | 4817 ms | 8147 ms | 0 |

### Mixed **send + receive** simultaneously (4,000 ops each)

| workers | throughput | p50 | p99 | errors |
|---|---|---|---|---|
| 50  | 189 msg/s | 51 ms  | 2743 ms | 0 |
| 100 | 206 msg/s | 86 ms  | 3948 ms | 0 |
| 200 | 197 msg/s | 205 ms | 6643 ms | 0 |

**Reading the tables:** throughput is flat (~200/s) and errors are zero at every
concurrency level — the classic signature of a single serialized writer. What
grows with concurrency is *latency*, because writers queue behind each other.

---

## What was fixed (committed)

1. **Thread-local connection pooling** (`database/database.py`) — `db.connect()`
   now returns a reentrant, per-thread pooled connection instead of opening a new
   one every call. Safe because all 306 call sites use `with db.connect()`; the
   proxy tracks nesting so only the outermost scope commits. **~20× per-message.**
2. **WAL write tuning** — `synchronous=NORMAL` (no per-commit fsync under WAL),
   `busy_timeout`, `temp_store=MEMORY`, `cache_size`.
3. **Removed hot-path `print()` debug** in `core/conversation_store.py` and the
   WhatsApp sender — these fired on every message, slowing the path and flooding
   logs in production.
4. Confirmed the hot lookups are already indexed (conversation state, customer
   identity, conversation events) — no missing index.

---

## To actually handle 10,000 msg/sec — the options (an architecture decision)

SQLite cannot sustain this; it is a single-writer embedded database. The realistic
paths, in order of effort:

### A. Absorb bursts with a fast-ack write queue — ✅ IMPLEMENTED (opt-in)
If "10k at the same moment" means a **spike** (10k messages arriving in a short
burst, not 10k/s forever): accept each webhook instantly into a bounded queue,
return `200` immediately, and drain into SQLite with a small worker pool.

Implemented in `core/ingest_queue.py`, wired into the WhatsApp-QR webhook, **off by
default**. Enable with `INGEST_ASYNC=true` (`INGEST_QUEUE_MAX`, `INGEST_WORKERS`).
Measured: **10,000 messages accepted in ~69 ms (~145,000 msg/s acceptance)** — so a
burst returns instantly with no webhook timeouts and no Meta/WhatsApp retry storms;
the DB then drains in the background at the ~200/s ceiling. Bounded queue +
**synchronous fallback when full** means memory is capped and no message is ever
dropped. **Caveats:** the queue is in-memory (a crash loses whatever is still
queued — a durable append-log front-end is the next upgrade), and a truly
*sustained* 10k/s still overruns any SQLite-backed queue (use Option B for that).
Currently wired only into the WhatsApp-QR webhook as the reference path; extending
to the Meta/Cloud webhooks is a small, mechanical follow-up.

### B. Migrate the database to PostgreSQL (high effort, the real fix for sustained load)
PostgreSQL has genuine concurrent writers (MVCC row-level locking), so 100 companies
writing at once scale near-linearly instead of serializing. This is the correct
answer for *sustained* 10k/s. Scope: the app is SQLite-specific in ~300 places
(`db.connect()`, `PRAGMA`, `INSERT OR IGNORE`, `sqlite3.Row`, autoincrement, the
JSONL conversation store). A migration means an abstraction layer over the driver,
translating the SQL dialect, provisioning a Postgres server, and re-running the full
test suite against it. **This needs your decision** (hosting, cost, ops) before it
can start.

### C. Horizontal scale (with B) — run multiple app instances behind a load
balancer against one Postgres. Only meaningful once the DB is Postgres; multiple
instances against one SQLite file re-serialize on the file lock.

---

## Recommendation

The connection-pooling fix already delivered a **20× improvement** and the platform
now handles **~200 msg/s cleanly with zero errors** — comfortably enough for
realistic traffic and modest bursts. For the stated **10k/s across 100 companies**:

- If it's **bursty** → implement **Option A** (self-contained, no new infra).
- If it's **sustained** → commit to **Option B** (PostgreSQL). This is an
  infrastructure decision only you can make; once made, the migration can proceed.
