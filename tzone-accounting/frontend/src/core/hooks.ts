/**
 * The extension bus — the client half of the same idea as the backend's `HookBus`.
 *
 * Modules extend one another only through named hooks. `invoicing` contributes a posting rule;
 * `documents` collects posting rules. Neither imports the other, so adding the tenth document
 * type changes nothing that already exists.
 */

interface Handler {
  sequence: number;
  order: number;
  module: string;
  fn: (...args: never[]) => unknown;
}

export class HookBus {
  private handlers = new Map<string, Handler[]>();
  private counter = 0;
  private currentModule = "core";

  bindingModule(key: string): void {
    this.currentModule = key;
  }

  on(hook: string, fn: (...args: never[]) => unknown, sequence = 10): void {
    const list = this.handlers.get(hook) ?? [];
    list.push({ sequence, order: ++this.counter, module: this.currentModule, fn });
    list.sort((a, b) => a.sequence - b.sequence || a.order - b.order);
    this.handlers.set(hook, list);
  }

  /** Gather one contribution per handler, dropping `undefined`/`null`. */
  collect<T>(hook: string, ...args: unknown[]): T[] {
    const results: T[] = [];
    for (const handler of this.handlers.get(hook) ?? []) {
      const value = (handler.fn as (...a: unknown[]) => T | undefined)(...args);
      if (value !== undefined && value !== null) results.push(value);
    }
    return results;
  }

  /** Fire and forget. */
  emit(hook: string, ...args: unknown[]): void {
    for (const handler of this.handlers.get(hook) ?? []) {
      (handler.fn as (...a: unknown[]) => unknown)(...args);
    }
  }

  async emitAsync(hook: string, ...args: unknown[]): Promise<void> {
    for (const handler of this.handlers.get(hook) ?? []) {
      await (handler.fn as (...a: unknown[]) => unknown)(...args);
    }
  }

  describe(): Record<string, string[]> {
    const out: Record<string, string[]> = {};
    for (const [hook, list] of [...this.handlers.entries()].sort()) {
      out[hook] = list.map((h) => `${h.module} (seq ${h.sequence})`);
    }
    return out;
  }
}
