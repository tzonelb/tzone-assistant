import { ensureSettings } from "../../core/settings";
import type { ModuleManifest } from "../../core/types";
import { baseTranslations } from "./i18n";
import { LoginPage } from "./LoginPage";
import { ModulesPage } from "./ModulesPage";
import { SettingsPage } from "./SettingsPage";

export const baseModule: ModuleManifest = {
  key: "base",
  name: "Base",
  nameAr: "الأساس",
  version: "1.0.0",
  summary: "Sign-in, the application shell, company settings and the module browser.",
  category: "Core",
  depends: [],
  sequence: 1,

  setup(ctx) {
    ctx.addEntity({ name: "settings", store: "settings", indexes: "id" });

    ctx.addTranslations(baseTranslations);
    ctx.addSettingsDefaults({
      company_name: "T-ZONE",
      language: "ar",
      lock_date: null,
    });

    ctx.addRoute({ path: "/login", element: LoginPage, standalone: true });
    ctx.addRoute({ path: "/settings", element: SettingsPage });
    ctx.addRoute({ path: "/modules", element: ModulesPage });

    ctx.addMenu({ path: "/settings", labelKey: "menu.settings", icon: "⚙", section: "system", sequence: 900 });
    ctx.addMenu({ path: "/modules", labelKey: "menu.modules", icon: "🧩", section: "system", sequence: 910 });

    ctx.addSeed(ensureSettings);
  },
};
