/**
 * The local database — the working copy of the company ledger.
 *
 * The schema is not written anywhere: it is assembled from every table the installed modules
 * declared. Installing a module later adds its tables on the next boot, which is handled by
 * bumping the Dexie version whenever the assembled schema changes shape.
 */

import Dexie, { type Table } from "dexie";
import { getRegistry } from "./registry";
import { readSetting, writeSetting } from "./storage";

export interface OutboxOp {
  seq?: number;
  entity: string;
  entityId: string;
  op: "upsert" | "delete";
  payload: Record<string, unknown>;
  status: "pending" | "failed";
  tries: number;
  error?: string;
  queuedAt: string;
}

/** Kernel-owned tables. Modules never touch these directly. */
export const KERNEL_STORES: Record<string, string> = {
  outbox: "++seq, entity, entityId, status",
  sync_state: "key",
  // Device-local document counters. Deliberately never replicated: each terminal owns its own
  // number series, which is what makes numbering work offline (docs/OFFLINE_SYNC.md §6).
  local_counters: "kind",
};

const VERSION_KEY = "tzone.db.version";
const SIGNATURE_KEY = "tzone.db.signature";

export type AppDatabase = Dexie & {
  outbox: Table<OutboxOp, number>;
  sync_state: Table<{ key: string; value: unknown }, string>;
};

let database: AppDatabase | null = null;

function signatureOf(stores: Record<string, string>): string {
  return Object.keys(stores)
    .sort()
    .map((name) => `${name}:${stores[name]}`)
    .join("|");
}

/**
 * Dexie needs a monotonically increasing integer version. The set of tables depends on which
 * modules are installed, so the version is derived: whenever the assembled schema differs from
 * the one this browser last opened, the stored version is incremented and Dexie migrates.
 */
function versionFor(stores: Record<string, string>): number {
  const signature = signatureOf(stores);
  const previous = readSetting(SIGNATURE_KEY);
  let version = Number(readSetting(VERSION_KEY) ?? "0") || 1;
  if (previous !== signature) {
    version = previous === null ? 1 : version + 1;
    writeSetting(SIGNATURE_KEY, signature);
    writeSetting(VERSION_KEY, String(version));
  }
  return version;
}

export function openDatabase(name = "tzone-accounting"): AppDatabase {
  if (database) return database;
  const stores = { ...getRegistry().schema(), ...KERNEL_STORES };
  const db = new Dexie(name) as AppDatabase;
  db.version(versionFor(stores)).stores(stores);
  database = db;
  return db;
}

export function db(): AppDatabase {
  if (!database) return openDatabase();
  return database;
}

export function table<T = Record<string, unknown>>(store: string): Table<T, string> {
  return db().table(store) as Table<T, string>;
}

/** Test helper: forget the open handle so the next call rebuilds from a fresh registry. */
export function resetDatabase(): void {
  database?.close();
  database = null;
}
