/**
 * The installed module set.
 *
 * This list is the *only* place the application names its modules. Adding a capability means
 * writing a module directory and adding one line here; removing one means deleting the line.
 * Load order is not this list's concern — the kernel resolves it from each manifest's `depends`.
 */

import type { ModuleManifest } from "../core/types";
import { accountingModule } from "./accounting";
import { baseModule } from "./base";
import { catalogModule } from "./catalog";
import { dashboardModule } from "./dashboard";
import { documentsModule } from "./documents";
import { invoicingModule } from "./invoicing";
import { partnersModule } from "./partners";
import { paymentsModule } from "./payments";
import { reportsModule } from "./reports";

export const ALL_MODULES: ModuleManifest[] = [
  baseModule,
  dashboardModule,
  accountingModule,
  partnersModule,
  catalogModule,
  documentsModule,
  invoicingModule,
  paymentsModule,
  reportsModule,
];
