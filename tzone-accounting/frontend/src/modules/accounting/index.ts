import { DEFAULT_CURRENCIES } from "../../core/money";
import type { ModuleManifest } from "../../core/types";
import { ChartOfAccountsPage } from "./ChartOfAccountsPage";
import { accountingTranslations } from "./i18n";
import { JournalPage } from "./JournalPage";
import { seedChartOfAccounts } from "./seed";

export const accountingModule: ModuleManifest = {
  key: "accounting",
  name: "Accounting core",
  nameAr: "النواة المحاسبية",
  version: "1.0.0",
  summary: "The chart of accounts and the double-entry journal that every other module posts into.",
  category: "Accounting",
  depends: ["base"],
  sequence: 10,

  setup(ctx) {
    ctx.addEntity({ name: "account", store: "accounts", indexes: "id, code, type, parent_id" });
    ctx.addEntity({
      name: "journal_entry",
      store: "journal_entries",
      indexes: "id, date, status, entry_no, [source_kind+source_id]",
    });

    ctx.addTranslations(accountingTranslations);
    ctx.addSettingsDefaults({
      base_currency: "USD",
      currencies: DEFAULT_CURRENCIES,
      fiscal_year_start: "01-01",
      // Modules resolve accounts by role, never by hardcoded code, so a company can renumber
      // its chart without any module changing.
      account_roles: {
        receivable: "acc-1130",
        payable: "acc-2110",
        tax_receivable: "acc-1150",
        tax_payable: "acc-2120",
        sales: "acc-4100",
        cogs: "acc-5100",
        opening_equity: "acc-3900",
        cash: "acc-1110",
        bank: "acc-1120",
      },
    });

    ctx.addRoute({ path: "/accounts", element: ChartOfAccountsPage });
    ctx.addRoute({ path: "/journal", element: JournalPage });

    ctx.addMenu({ path: "/accounts", labelKey: "menu.accounts", icon: "🗂", section: "ledger", sequence: 10 });
    ctx.addMenu({ path: "/journal", labelKey: "menu.journal", icon: "📒", section: "ledger", sequence: 20 });

    ctx.addSeed(seedChartOfAccounts);
  },
};
