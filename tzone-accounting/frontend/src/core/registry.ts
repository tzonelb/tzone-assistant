/**
 * The client kernel: resolve the module graph, run each module's `setup`, and hold what they
 * contributed.
 *
 * Same three passes as the server (`backend/app/core/registry.py`): manifests are declared up
 * front, dependencies are resolved into a deterministic order, then each module registers its
 * tables, screens, menus, translations and hooks. Nothing in this file mentions accounting.
 */

import { HookBus } from "./hooks";
import type {
  DashboardCard,
  EntityDef,
  LocaleBundle,
  MenuItem,
  ModuleContext,
  ModuleManifest,
  RouteDef,
  Seed,
  SettingsPanel,
} from "./types";

export class ModuleError extends Error {}

export class Registry {
  readonly hooks = new HookBus();
  readonly modules = new Map<string, ModuleManifest>();
  readonly entities = new Map<string, EntityDef>();
  readonly localStores = new Map<string, string>();
  readonly routes: RouteDef[] = [];
  readonly menu: MenuItem[] = [];
  readonly seeds: Array<{ module: string; seed: Seed }> = [];
  readonly dashboardCards: DashboardCard[] = [];
  readonly settingsPanels: SettingsPanel[] = [];
  readonly settingsDefaults: Record<string, unknown> = {};
  readonly translations: LocaleBundle = { en: {}, ar: {} };
  installOrder: string[] = [];

  private loading = "core";
  private loaded = false;

  private context(): ModuleContext {
    const registry = this;
    return {
      get key() {
        return registry.loading;
      },
      addEntity(entity) {
        const existing = registry.entities.get(entity.name);
        if (existing) {
          throw new ModuleError(
            `module '${registry.loading}': entity '${entity.name}' is already declared by '${existing.module}'`,
          );
        }
        registry.entities.set(entity.name, { ...entity, module: registry.loading });
      },
      addLocalStore(store, indexes) {
        registry.localStores.set(store, indexes);
      },
      addRoute(route) {
        registry.routes.push(route);
      },
      addMenu(item) {
        registry.menu.push({ sequence: 100, ...item });
      },
      addTranslations(bundle) {
        Object.assign(registry.translations.en, bundle.en);
        Object.assign(registry.translations.ar, bundle.ar);
      },
      addSeed(seed) {
        registry.seeds.push({ module: registry.loading, seed });
      },
      addSettingsDefaults(values) {
        Object.assign(registry.settingsDefaults, values);
      },
      addDashboardCard(card) {
        registry.dashboardCards.push({ sequence: 100, ...card });
      },
      addSettingsPanel(panel) {
        registry.settingsPanels.push({ sequence: 100, ...panel });
      },
      on(hook, handler, sequence) {
        registry.hooks.on(hook, handler, sequence);
      },
    };
  }

  load(manifests: ModuleManifest[], only?: string[]): Registry {
    if (this.loaded) return this;
    const selected = select(manifests, only);
    const context = this.context();

    for (const manifest of resolveOrder(selected)) {
      this.loading = manifest.key;
      this.hooks.bindingModule(manifest.key);
      this.modules.set(manifest.key, manifest);
      manifest.setup(context);
      this.installOrder.push(manifest.key);
    }

    this.loading = "core";
    this.hooks.bindingModule("core");
    this.menu.sort((a, b) => (a.sequence ?? 100) - (b.sequence ?? 100));
    this.dashboardCards.sort((a, b) => (a.sequence ?? 100) - (b.sequence ?? 100));
    this.settingsPanels.sort((a, b) => (a.sequence ?? 100) - (b.sequence ?? 100));
    this.loaded = true;
    return this;
  }

  /** The Dexie schema, assembled from every declared table. */
  schema(): Record<string, string> {
    const stores: Record<string, string> = {};
    for (const entity of this.entities.values()) stores[entity.store] = entity.indexes;
    for (const [store, indexes] of this.localStores) stores[store] = indexes;
    return stores;
  }

  entityForStore(store: string): EntityDef | undefined {
    for (const entity of this.entities.values()) {
      if (entity.store === store) return entity;
    }
    return undefined;
  }

  describe() {
    return {
      installOrder: this.installOrder,
      modules: [...this.modules.values()].map((m) => ({
        key: m.key,
        name: m.name,
        nameAr: m.nameAr,
        version: m.version,
        summary: m.summary,
        category: m.category,
        depends: m.depends,
        entities: [...this.entities.values()]
          .filter((e) => e.module === m.key)
          .map((e) => e.name),
        screens: this.routes.length,
      })),
      hooks: this.hooks.describe(),
    };
  }
}

function select(manifests: ModuleManifest[], only?: string[]): ModuleManifest[] {
  const byKey = new Map(manifests.map((m) => [m.key, m]));
  if (!only) return manifests;

  const wanted = new Map<string, ModuleManifest>();
  const stack = [...only];
  while (stack.length) {
    const key = stack.pop()!;
    if (wanted.has(key)) continue;
    const manifest = byKey.get(key);
    if (!manifest) throw new ModuleError(`unknown module '${key}'`);
    wanted.set(key, manifest);
    stack.push(...manifest.depends);
  }
  return [...wanted.values()];
}

/** Topological sort; ties broken by `sequence` then key so the order is machine-independent. */
export function resolveOrder(manifests: ModuleManifest[]): ModuleManifest[] {
  const byKey = new Map(manifests.map((m) => [m.key, m]));
  for (const manifest of manifests) {
    for (const dependency of manifest.depends) {
      if (!byKey.has(dependency)) {
        throw new ModuleError(
          `module '${manifest.key}' depends on '${dependency}', which is not installed`,
        );
      }
    }
  }

  const ordered: ModuleManifest[] = [];
  const placed = new Set<string>();
  const remaining = new Map(byKey);

  while (remaining.size) {
    const ready = [...remaining.values()].filter((m) =>
      m.depends.every((d) => placed.has(d)),
    );
    if (!ready.length) {
      throw new ModuleError(
        `dependency cycle between modules: ${[...remaining.keys()].sort().join(", ")}`,
      );
    }
    ready.sort((a, b) => (a.sequence ?? 100) - (b.sequence ?? 100) || a.key.localeCompare(b.key));
    for (const manifest of ready) {
      ordered.push(manifest);
      placed.add(manifest.key);
      remaining.delete(manifest.key);
    }
  }
  return ordered;
}

let instance: Registry | null = null;

export function getRegistry(): Registry {
  if (!instance) throw new ModuleError("registry not installed yet — call installModules() first");
  return instance;
}

export function installModules(manifests: ModuleManifest[], only?: string[]): Registry {
  instance = new Registry().load(manifests, only);
  return instance;
}

export function resetRegistry(): void {
  instance = null;
}
