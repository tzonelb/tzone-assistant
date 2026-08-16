/**
 * Boot sequence: open the assembled database, mint the device identity, then run each installed
 * module's seeds in install order. Seeds are idempotent, so this is safe on every start — and
 * installing a module later simply means its seed runs for the first time on the next boot.
 */

import { openDatabase } from "./db";
import { getRegistry } from "./registry";
import { deviceId } from "./repository";

export async function bootstrap(): Promise<void> {
  openDatabase();
  deviceId();
  for (const { seed } of getRegistry().seeds) {
    await seed();
  }
}
