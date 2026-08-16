/** The kernel's own tests: dependency resolution, isolation, and the assembled schema. */

import { beforeEach, describe, expect, it } from "vitest";
import { installModules, ModuleError, resetRegistry, resolveOrder } from "./registry";
import type { ModuleManifest } from "./types";
import { ALL_MODULES } from "../modules";

function stub(key: string, depends: string[] = [], sequence = 100): ModuleManifest {
  return {
    key,
    name: key,
    nameAr: key,
    version: "1.0.0",
    summary: `stub ${key}`,
    category: "Test",
    depends,
    sequence,
    setup() {},
  };
}

beforeEach(() => resetRegistry());

describe("dependency resolution", () => {
  it("loads dependencies before dependents", () => {
    const order = resolveOrder([stub("c", ["b"]), stub("a"), stub("b", ["a"])]).map((m) => m.key);
    expect(order).toEqual(["a", "b", "c"]);
  });

  it("is deterministic regardless of input order", () => {
    const modules = [stub("c", ["b"]), stub("a"), stub("b", ["a"]), stub("d", ["a"])];
    const forwards = resolveOrder(modules).map((m) => m.key);
    const backwards = resolveOrder([...modules].reverse()).map((m) => m.key);
    expect(forwards).toEqual(backwards);
  });

  it("breaks ties by sequence", () => {
    const order = resolveOrder([stub("late", [], 90), stub("early", [], 10)]).map((m) => m.key);
    expect(order).toEqual(["early", "late"]);
  });

  it("names the missing dependency", () => {
    expect(() => resolveOrder([stub("a", ["ghost"])])).toThrow(/depends on 'ghost'/);
  });

  it("detects a cycle instead of hanging", () => {
    expect(() => resolveOrder([stub("a", ["b"]), stub("b", ["a"])])).toThrow(/cycle/);
  });
});

describe("the real module graph", () => {
  it("resolves and every dependency precedes its dependent", () => {
    const registry = installModules(ALL_MODULES);
    const position = new Map(registry.installOrder.map((key, index) => [key, index]));
    for (const manifest of ALL_MODULES) {
      for (const dependency of manifest.depends) {
        expect(position.get(dependency)!).toBeLessThan(position.get(manifest.key)!);
      }
    }
  });

  it("assembles a schema containing every module's tables plus the kernel's", () => {
    const registry = installModules(ALL_MODULES);
    const schema = registry.schema();
    expect(Object.keys(schema)).toEqual(
      expect.arrayContaining(["accounts", "journal_entries", "partners", "items", "documents"]),
    );
  });

  it("registers a route and a translation for every menu entry", () => {
    const registry = installModules(ALL_MODULES);
    const paths = new Set(registry.routes.map((route) => route.path));
    for (const item of registry.menu) {
      expect(paths.has(item.path)).toBe(true);
      expect(registry.translations.en[item.labelKey]).toBeDefined();
      expect(registry.translations.ar[item.labelKey]).toBeDefined();
    }
  });

  it("translates every key in both languages", () => {
    const registry = installModules(ALL_MODULES);
    const english = Object.keys(registry.translations.en).sort();
    const arabic = Object.keys(registry.translations.ar).sort();
    expect(arabic).toEqual(english);
  });
});

describe("module isolation", () => {
  it("refuses two modules claiming the same entity", () => {
    const first = { ...stub("one"), setup: (ctx: never) => voidEntity(ctx, "thing") };
    const second = { ...stub("two"), setup: (ctx: never) => voidEntity(ctx, "thing") };
    expect(() => installModules([first, second])).toThrow(ModuleError);
  });

  it("installs a subset with its dependencies and nothing else", () => {
    const registry = installModules(ALL_MODULES, ["reports"]);
    expect(registry.modules.has("accounting")).toBe(true);
    expect(registry.modules.has("base")).toBe(true);
    expect(registry.modules.has("invoicing")).toBe(false);
    // Uninstalled modules contribute no screens.
    expect(registry.routes.some((route) => route.path === "/sales-invoices")).toBe(false);
  });
});

// Helper kept out of the test body so the `never` cast does not obscure the assertion above.
function voidEntity(ctx: never, name: string): void {
  (ctx as unknown as { addEntity(e: unknown): void }).addEntity({
    name,
    store: name,
    indexes: "id",
  });
}
