import { useI18n } from "../../core/i18n";
import { getRegistry } from "../../core/registry";
import { EmptyState, Page } from "../../core/ui";
import type { ModuleManifest } from "../../core/types";

/**
 * The dashboard owns no data. It renders whatever cards the installed modules registered, in
 * their declared order — so a new module can put a figure on the front page without this file
 * ever being edited.
 */
function DashboardPage() {
  const { t } = useI18n();
  const cards = getRegistry().dashboardCards;

  return (
    <Page title={t("dashboard.title")} subtitle={t("dashboard.subtitle")}>
      {cards.length ? (
        <div className="card-grid">
          {cards.map((card) => {
            const Render = card.render;
            return <Render key={card.id} />;
          })}
        </div>
      ) : (
        <EmptyState message={t("dashboard.empty")} />
      )}
    </Page>
  );
}

export const dashboardModule: ModuleManifest = {
  key: "dashboard",
  name: "Dashboard",
  nameAr: "لوحة المتابعة",
  version: "1.0.0",
  summary: "The front page. Renders cards contributed by other modules and owns no data itself.",
  category: "Core",
  depends: ["base"],
  sequence: 5,

  setup(ctx) {
    ctx.addRoute({ path: "/dashboard", element: DashboardPage });
    ctx.addMenu({
      path: "/dashboard",
      labelKey: "dashboard.title",
      icon: "🏠",
      section: "general",
      sequence: 1,
    });
    ctx.addTranslations({
      en: {
        "dashboard.title": "Dashboard",
        "dashboard.subtitle": "Today's position, from your local books.",
        "dashboard.empty": "No modules have contributed a card yet.",
      },
      ar: {
        "dashboard.title": "لوحة المتابعة",
        "dashboard.subtitle": "وضع اليوم، من دفاترك المحلية.",
        "dashboard.empty": "لم يضف أي موديول بطاقة بعد.",
      },
    });
  },
};
