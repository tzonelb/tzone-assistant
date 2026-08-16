/**
 * The sync engine.
 *
 * It drains the outbox and pulls remote changes, in that order, whenever the device is online.
 * It is entity-agnostic: whatever tables the installed modules declared are what it replicates.
 *
 * Protocol, conflict policy and retry rules: docs/OFFLINE_SYNC.md.
 */

import { ApiError, OfflineError, request } from "./api";
import { db, type OutboxOp } from "./db";
import { getRegistry } from "./registry";
import { applyRemote, deviceId } from "./repository";
import type { Envelope } from "./types";

const CURSOR_KEY = "cursor";
const PUSH_BATCH = 200;
const PULL_LIMIT = 500;
const MAX_BACKOFF_MS = 5 * 60_000;

export interface SyncResult {
  pushed: number;
  pulled: number;
  rejected: Array<{ entity: string; id: string; reason: string }>;
  cursor: number;
}

export type SyncStatus = "idle" | "syncing" | "offline" | "error";

export interface SyncSnapshot {
  status: SyncStatus;
  pending: number;
  failed: number;
  lastSyncAt: string | null;
  lastError: string | null;
}

type Listener = (snapshot: SyncSnapshot) => void;

interface PushResponse {
  accepted: number[];
  rejected: Array<{ seq: number; id: string; entity: string; reason: string }>;
  cursor: number;
  assigned: Record<string, Record<string, unknown>>;
}

interface PullResponse {
  changes: Array<{ entity: string; change_seq: number; record: Envelope }>;
  cursor: number;
  has_more: boolean;
}

export class SyncEngine {
  private listeners = new Set<Listener>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private running = false;
  private backoffMs = 0;
  private snapshot: SyncSnapshot = {
    status: "idle",
    pending: 0,
    failed: 0,
    lastSyncAt: null,
    lastError: null,
  };

  constructor(private readonly intervalMs = 20_000) {}

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    listener(this.snapshot);
    return () => void this.listeners.delete(listener);
  }

  private publish(patch: Partial<SyncSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of this.listeners) listener(this.snapshot);
  }

  start(): void {
    if (this.timer) return;
    if (typeof window !== "undefined") {
      // Coming back online is the interesting moment: drain immediately rather than waiting
      // out the poll interval.
      window.addEventListener("online", this.kick);
      window.addEventListener("offline", this.kick);
    }
    this.schedule(0);
  }

  stop(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (typeof window !== "undefined") {
      window.removeEventListener("online", this.kick);
      window.removeEventListener("offline", this.kick);
    }
  }

  private kick = (): void => {
    this.schedule(0);
  };

  private schedule(delay: number): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.tick(), delay);
  }

  private async tick(): Promise<void> {
    await this.syncOnce();
    this.schedule(Math.max(this.intervalMs, this.backoffMs));
  }

  async refreshCounts(): Promise<void> {
    const pending = await db().outbox.where("status").equals("pending").count();
    const failed = await db().outbox.where("status").equals("failed").count();
    this.publish({ pending, failed });
  }

  async syncOnce(): Promise<SyncResult> {
    const empty: SyncResult = { pushed: 0, pulled: 0, rejected: [], cursor: await this.cursor() };
    if (this.running) return empty;
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      this.publish({ status: "offline" });
      await this.refreshCounts();
      return empty;
    }

    this.running = true;
    this.publish({ status: "syncing" });
    try {
      const pushed = await this.push();
      const pulled = await this.pull();
      this.backoffMs = 0;
      this.publish({
        status: "idle",
        lastSyncAt: new Date().toISOString(),
        lastError: null,
      });
      await this.refreshCounts();
      return { ...pushed, pulled: pulled.pulled, cursor: pulled.cursor };
    } catch (error) {
      // Transport failures are expected and temporary; back off and keep the queue intact.
      const offline = error instanceof OfflineError;
      this.backoffMs = Math.min(Math.max(this.backoffMs * 2, 2_000), MAX_BACKOFF_MS);
      this.publish({
        status: offline ? "offline" : "error",
        lastError: offline ? null : String((error as Error).message ?? error),
      });
      await this.refreshCounts();
      return empty;
    } finally {
      this.running = false;
    }
  }

  private async cursor(): Promise<number> {
    const row = await db().sync_state.get(CURSOR_KEY);
    return Number(row?.value ?? 0);
  }

  private async setCursor(value: number): Promise<void> {
    await db().sync_state.put({ key: CURSOR_KEY, value });
  }

  private async push(): Promise<SyncResult> {
    const result: SyncResult = { pushed: 0, pulled: 0, rejected: [], cursor: 0 };
    for (;;) {
      const batch = await db()
        .outbox.where("status")
        .equals("pending")
        .limit(PUSH_BATCH)
        .toArray();
      if (!batch.length) break;

      const response = await request<PushResponse>("/api/sync/push", {
        method: "POST",
        body: {
          device_id: deviceId(),
          ops: batch.map((op) => ({
            seq: op.seq,
            entity: op.entity,
            id: op.entityId,
            op: op.op,
            record: op.payload,
          })),
        },
      });

      const accepted = new Set(response.accepted);
      const rejected = new Map(response.rejected.map((r) => [r.seq, r]));

      await db().transaction("rw", db().outbox, async () => {
        for (const op of batch) {
          const seq = op.seq!;
          if (accepted.has(seq)) {
            await db().outbox.delete(seq);
            continue;
          }
          const rejection = rejected.get(seq);
          if (rejection) {
            // A rejected op means the two ledgers disagree. Keep it, flagged, and show it.
            await db().outbox.update(seq, {
              status: "failed",
              error: rejection.reason,
              tries: op.tries + 1,
            });
            result.rejected.push({
              entity: rejection.entity,
              id: rejection.id,
              reason: rejection.reason,
            });
          }
        }
      });

      // Server-assigned fields (the gapless legal number) come back on the push response.
      for (const [recordId, overrides] of Object.entries(response.assigned ?? {})) {
        const op = batch.find((candidate) => candidate.entityId === recordId);
        if (!op) continue;
        await this.mergeOverrides(op, overrides);
      }

      result.pushed += accepted.size;
      if (batch.length < PUSH_BATCH) break;
    }
    return result;
  }

  private async mergeOverrides(op: OutboxOp, overrides: Record<string, unknown>): Promise<void> {
    const definition = getRegistry().entities.get(op.entity);
    if (!definition) return;
    const current = (await db().table(definition.store).get(op.entityId)) as
      | Record<string, unknown>
      | undefined;
    if (!current) return;
    // Written without an outbox op on purpose: the server is the author of these fields.
    await db().table(definition.store).put({ ...current, ...overrides });
  }

  private async pull(): Promise<{ pulled: number; cursor: number }> {
    let cursor = await this.cursor();
    let pulled = 0;

    for (;;) {
      const response = await request<PullResponse>(
        `/api/sync/pull?since=${cursor}&limit=${PULL_LIMIT}`,
      );
      if (!response.changes.length) {
        cursor = response.cursor;
        break;
      }

      for (const change of response.changes) {
        if (!getRegistry().entities.has(change.entity)) continue; // module not installed here
        if (await this.shouldApply(change.entity, change.record)) {
          await applyRemote(change.entity, change.record);
          pulled += 1;
        }
      }

      cursor = response.cursor;
      await this.setCursor(cursor);
      if (!response.has_more) break;
    }

    await this.setCursor(cursor);
    return { pulled, cursor };
  }

  /**
   * Last-writer-wins on `updated_at`, with one override: anything still queued in the outbox
   * for this record wins, because it has not had its turn on the server yet.
   */
  private async shouldApply(entity: string, incoming: Envelope): Promise<boolean> {
    const queued = await db()
      .outbox.where("entityId")
      .equals(incoming.id)
      .filter((op) => op.entity === entity && op.status === "pending")
      .count();
    if (queued > 0) return false;

    const definition = getRegistry().entities.get(entity)!;
    const local = (await db().table(definition.store).get(incoming.id)) as Envelope | undefined;
    if (!local) return true;
    if (local.updated_at !== incoming.updated_at) return incoming.updated_at > local.updated_at;
    return incoming.origin >= local.origin;
  }

  /** Re-queue an op the server rejected, after the user has fixed the underlying data. */
  async retryFailed(seq: number): Promise<void> {
    await db().outbox.update(seq, { status: "pending", error: undefined });
    this.schedule(0);
  }

  /** Abandon a rejected op. The local record keeps whatever it holds; the server never sees it. */
  async discardFailed(seq: number): Promise<void> {
    await db().outbox.delete(seq);
    await this.refreshCounts();
  }
}

export const syncEngine = new SyncEngine();

export function isAuthError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}
