/**
 * The module contract.
 *
 * A frontend module is a manifest plus a `setup(ctx)` function — exactly like the backend.
 * Everything a module adds to the running application (tables, screens, menu entries,
 * translations, posting rules, report definitions, dashboard cards) it adds through `ctx`.
 * No module imports another module's internals; they meet at hooks and at these registries.
 */

import type { ComponentType, ReactNode } from "react";

export type AccountType = "asset" | "liability" | "equity" | "income" | "expense";
export type DocumentStatus = "draft" | "posted" | "partial" | "paid" | "void";
export type EntryStatus = "draft" | "posted" | "void";

/** The replication envelope every syncable record carries. See docs/OFFLINE_SYNC.md §2. */
export interface Envelope {
  id: string;
  rev: number;
  updated_at: string;
  deleted: boolean;
  origin: string;
}

export interface EntityDef {
  /** Must match the backend entity name — it is the key on the wire. */
  name: string;
  /** Dexie table name. */
  store: string;
  /** Dexie index specification, e.g. "id, code, [type+code]". */
  indexes: string;
  /** Child collections stored inline on the record (journal lines) travel with it. */
  module?: string;
}

export interface RouteDef {
  path: string;
  element: ComponentType;
  /** Rendered outside the authenticated shell (the login screen). */
  standalone?: boolean;
}

export interface MenuItem {
  path: string;
  labelKey: string;
  icon: string;
  section?: string;
  sequence?: number;
}

export interface DashboardCard {
  id: string;
  sequence?: number;
  render: ComponentType;
}

export interface SettingsPanel {
  id: string;
  titleKey: string;
  sequence?: number;
  render: ComponentType;
}

export type Translations = Record<string, string>;

export interface LocaleBundle {
  en: Translations;
  ar: Translations;
}

export type Seed = () => Promise<void>;

/** Everything a module may contribute. Mirrors the backend `Registry`. */
export interface ModuleContext {
  readonly key: string;
  addEntity(entity: EntityDef): void;
  /** A local-only table (never replicated) — caches, drafts, UI state. */
  addLocalStore(store: string, indexes: string): void;
  addRoute(route: RouteDef): void;
  addMenu(item: MenuItem): void;
  addTranslations(bundle: LocaleBundle): void;
  addSeed(seed: Seed): void;
  addSettingsDefaults(values: Record<string, unknown>): void;
  addDashboardCard(card: DashboardCard): void;
  addSettingsPanel(panel: SettingsPanel): void;
  on<T = unknown>(hook: string, handler: (...args: never[]) => T, sequence?: number): void;
}

export interface ModuleManifest {
  key: string;
  name: string;
  nameAr: string;
  version: string;
  summary: string;
  category: string;
  depends: string[];
  sequence?: number;
  setup(ctx: ModuleContext): void;
}

export interface AppChildren {
  children?: ReactNode;
}
