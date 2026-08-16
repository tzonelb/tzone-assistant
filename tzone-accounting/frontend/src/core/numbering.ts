/**
 * Offline-safe document numbering.
 *
 * Two terminals working offline would both claim `INV-000042` if the counter were global, and
 * asking the server for a number defeats the point of working offline. So numbers are namespaced
 * by the device code assigned at first sign-in:
 *
 *     SI-A7-000042
 *
 * Globally unique with no coordination, and readable — the number says which terminal issued the
 * document. The gapless number a tax authority wants is assigned by the server on first sync and
 * stored alongside as `legal_no`.
 */

import { db } from "./db";
import { DEVICE_CODE_KEY } from "./repository";
import { readSetting } from "./storage";

export function deviceCode(): string {
  return readSetting(DEVICE_CODE_KEY) || "LOCAL";
}

export async function nextNumber(kind: string, prefix: string): Promise<string> {
  const counters = db().table<{ kind: string; value: number }, string>("local_counters");
  let value = 1;
  await db().transaction("rw", counters, async () => {
    const row = await counters.get(kind);
    value = (row?.value ?? 0) + 1;
    await counters.put({ kind, value });
  });
  return `${prefix}-${deviceCode()}-${String(value).padStart(6, "0")}`;
}

/** Peek without consuming — for showing the next number on an unsaved form. */
export async function peekNumber(kind: string, prefix: string): Promise<string> {
  const row = await db()
    .table<{ kind: string; value: number }, string>("local_counters")
    .get(kind);
  return `${prefix}-${deviceCode()}-${String((row?.value ?? 0) + 1).padStart(6, "0")}`;
}
