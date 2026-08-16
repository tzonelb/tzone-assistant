/**
 * The only way the UI writes data.
 *
 * Every write lands in IndexedDB and appends an outbox op **in the same Dexie transaction**. A
 * crash between the two is therefore impossible: there is never a saved record without its
 * outbox op, and never an op without its record. This is the whole basis of working offline —
 * the screen updates from the local table, and the server hears about it whenever it can.
 */

import { db, table, type OutboxOp } from "./db";
import { getRegistry } from "./registry";
import { readSetting, writeSetting } from "./storage";
import type { Envelope } from "./types";

export const DEVICE_ID_KEY = "tzone.device.id";
export const DEVICE_CODE_KEY = "tzone.device.code";

/**
 * Stable per-browser id, minted on first use and never regenerated. It stamps `origin` on every
 * record — which is what makes last-writer-wins deterministic — and it is the identity the
 * server hands a short `device_code` to for offline document numbering.
 */
export function deviceId(): string {
  let id = readSetting(DEVICE_ID_KEY);
  if (!id) {
    id = newId();
    writeSetting(DEVICE_ID_KEY, id);
  }
  return id;
}

export function nowIso(): string {
  return new Date().toISOString();
}

export function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `id-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function storeFor(entity: string): string {
  const definition = getRegistry().entities.get(entity);
  if (!definition) throw new Error(`no installed module declares entity '${entity}'`);
  return definition.store;
}

function stamp<T extends Partial<Envelope>>(record: T, previousRev: number | undefined): T & Envelope {
  return {
    ...record,
    id: record.id ?? newId(),
    rev: (previousRev ?? record.rev ?? 0) + 1,
    updated_at: nowIso(),
    deleted: record.deleted ?? false,
    origin: deviceId(),
  } as T & Envelope;
}

/** Insert or update a record and queue it for replication. Returns the stored record. */
export async function save<T extends Partial<Envelope>>(
  entity: string,
  record: T,
): Promise<T & Envelope> {
  const store = storeFor(entity);
  const database = db();
  let stored!: T & Envelope;

  await database.transaction("rw", database.table(store), database.outbox, async () => {
    const previous = record.id
      ? ((await database.table(store).get(record.id)) as Envelope | undefined)
      : undefined;
    stored = stamp(record, previous?.rev);
    await database.table(store).put(stored);
    await database.outbox.add({
      entity,
      entityId: stored.id,
      op: "upsert",
      payload: stored as unknown as Record<string, unknown>,
      status: "pending",
      tries: 0,
      queuedAt: stored.updated_at,
    } satisfies OutboxOp);
  });

  return stored;
}

/**
 * Soft-delete. Rows are never removed: a tombstone is what tells other devices the record is
 * gone. A hard delete would simply be re-created by the next pull from a device that still has
 * it.
 */
export async function remove(entity: string, id: string): Promise<void> {
  const store = storeFor(entity);
  const database = db();
  await database.transaction("rw", database.table(store), database.outbox, async () => {
    const previous = (await database.table(store).get(id)) as Envelope | undefined;
    if (!previous) return;
    const tombstone = { ...previous, deleted: true, rev: previous.rev + 1, updated_at: nowIso(), origin: deviceId() };
    await database.table(store).put(tombstone);
    await database.outbox.add({
      entity,
      entityId: id,
      op: "delete",
      payload: tombstone as unknown as Record<string, unknown>,
      status: "pending",
      tries: 0,
      queuedAt: tombstone.updated_at,
    } satisfies OutboxOp);
  });
}

/** Write without queuing — used by the sync engine when applying records pulled from the server. */
export async function applyRemote(entity: string, record: Envelope): Promise<void> {
  await table(storeFor(entity)).put(record as never);
}

export async function get<T>(entity: string, id: string): Promise<T | undefined> {
  return (await table(storeFor(entity)).get(id)) as T | undefined;
}

/** Every live record of an entity. Tombstones are filtered out here, once, for every caller. */
export async function list<T>(entity: string): Promise<T[]> {
  const rows = (await table(storeFor(entity)).toArray()) as unknown as Array<T & Envelope>;
  return rows.filter((row) => !row.deleted) as T[];
}

export async function pendingCount(): Promise<number> {
  return db().outbox.where("status").equals("pending").count();
}

export async function failedOps(): Promise<OutboxOp[]> {
  return db().outbox.where("status").equals("failed").toArray();
}
