import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Avatar,
  Button,
  Card,
  Chip,
  Dialog,
  Divider,
  Drawer,
  EmptyState,
  Field,
  Figure,
  IconButton,
  Input,
  Kicker,
  Num,
  Radio,
  RailLink,
  Segmented,
  Shell,
  Skeleton,
  SkeletonRows,
  Stat,
  StatGrid,
  Table,
  Tag,
  ToastStack,
  useToasts,
} from "../../theme/react.jsx";

// This package's --tz-* custom properties collide with this app's own
// pre-existing --tz-* namespace (styles/theme.css and others) if loaded
// on the main document — that already broke a real page once (Publish).
// Rendering inside an isolated iframe (its own document, its own :root)
// is the only way to demo this package without that risk.
function IsolatedFrame({ children }) {
  const iframeRef = useRef(null);
  const [mountNode, setMountNode] = useState(null);

  useEffect(() => {
    const iframe = iframeRef.current;
    const frameDocument = iframe.contentDocument;
    frameDocument.open();
    frameDocument.write(
      '<!DOCTYPE html><html><head><meta charset="utf-8">' +
      '<link rel="stylesheet" href="/src/theme/tokens.css">' +
      '<link rel="stylesheet" href="/src/theme/theme.css">' +
      '<style>html,body{margin:0;height:100%}#root{height:100%}</style>' +
      '</head><body><div id="root"></div></body></html>'
    );
    frameDocument.close();
    setMountNode(frameDocument.getElementById("root"));
  }, []);

  return (
    <>
      <iframe
        ref={iframeRef}
        title="Theme package preview (isolated)"
        style={{ width: "100%", height: "100%", border: 0, display: "block" }}
      />
      {mountNode ? <FramePortal node={mountNode}>{children}</FramePortal> : null}
    </>
  );
}

function FramePortal({ node, children }) {
  useEffect(() => {
    const root = createRoot(node);
    root.render(children);
    return () => root.unmount();
  }, [node, children]);
  return null;
}

// Standalone, isolated showcase of every component in src/theme/react.jsx.
// Every value below is placeholder demo copy — no app data, no existing
// page or component is touched or reused here.

const NAV_ITEMS_EN = [
  { key: "overview", label: "Overview", badge: null },
  { key: "inbox", label: "Inbox", badge: "12" },
  { key: "people", label: "People", badge: null },
  { key: "settings", label: "Settings", badge: null },
];

const NAV_ITEMS_AR = [
  { key: "overview", label: "نظرة عامة", badge: null },
  { key: "inbox", label: "الوارد", badge: "١٢" },
  { key: "people", label: "الأشخاص", badge: null },
  { key: "settings", label: "الإعدادات", badge: null },
];

const STAT_CELLS_EN = [
  { label: "Sample metric one", value: "128", note: "up from last period" },
  { label: "Sample metric two", value: "74%", note: "steady" },
  { label: "Sample metric three", value: "9", note: "needs attention" },
];

const STAT_CELLS_AR = [
  { label: "مؤشر تجريبي أول", value: "١٢٨", note: "أعلى من الفترة السابقة" },
  { label: "مؤشر تجريبي ثاني", value: "٪٧٤", note: "مستقر" },
  { label: "مؤشر تجريبي ثالث", value: "٩", note: "يحتاج متابعة" },
];

const TABLE_ROWS_EN = [
  ["Sample row one", "Type A", "Placeholder note"],
  ["Sample row two", "Type B", "Placeholder note"],
  ["Sample row three", "Type A", "Placeholder note"],
];

const TABLE_ROWS_AR = [
  ["سطر تجريبي أول", "نوع أ", "ملاحظة توضيحية"],
  ["سطر تجريبي ثاني", "نوع ب", "ملاحظة توضيحية"],
  ["سطر تجريبي ثالث", "نوع أ", "ملاحظة توضيحية"],
];

const COPY = {
  en: {
    dir: "ltr",
    lang: "en",
    switchLabel: "Switch to Arabic (RTL)",
    pageTitle: "Theme component preview",
    pageSubtitle: "Every component below is placeholder demo content — nothing here reads real app data.",
    sectionButtons: "Buttons",
    sectionTags: "Tags & chips",
    sectionType: "Type helpers",
    sectionCard: "Card",
    cardKicker: "Sample section",
    cardTitle: "Sample card title",
    cardBody: "This is placeholder body copy demonstrating the Card component's body slot.",
    cardMeta: "Sample meta line",
    sectionStats: "Stat grid",
    sectionTable: "Table",
    tableCol1: "Name",
    tableCol2: "Type",
    tableCol3: "Note",
    sectionFields: "Form fields",
    fieldLabel1: "Sample text field",
    fieldPlaceholder1: "Type something…",
    fieldLabel2: "Sample multiline field",
    fieldPlaceholder2: "Longer placeholder text…",
    segOptions: ["Option A", "Option B", "Option C"],
    radioLabel: "Sample checkbox option",
    sectionAvatar: "Avatar",
    sectionDrawer: "Drawer",
    openDrawer: "Open drawer",
    drawerTitle: "Sample drawer title",
    drawerBody: "Placeholder drawer body content.",
    drawerClose: "Close",
    sectionDialog: "Dialog",
    openDialog: "Open dialog",
    dialogTitle: "Sample dialog title",
    dialogBody: "Placeholder dialog body content.",
    dialogCancel: "Cancel",
    dialogConfirm: "Confirm",
    sectionEmpty: "Empty state",
    emptyTitle: "Nothing here yet",
    emptyDescription: "Placeholder description for an empty state.",
    emptyAction: "Sample action",
    sectionSkeleton: "Skeleton / loading",
    sectionToast: "Toast",
    pushToast: "Show a toast",
    toastMessage: "This is a sample toast message.",
    toastAction: "Undo",
  },
  ar: {
    dir: "rtl",
    lang: "ar",
    switchLabel: "التبديل للإنجليزية (LTR)",
    pageTitle: "معاينة مكوّنات الثيم",
    pageSubtitle: "كل المكوّنات تحت هي محتوى توضيحي فقط — ولا شي هون بيقرا بيانات حقيقية من التطبيق.",
    sectionButtons: "الأزرار",
    sectionTags: "الوسوم والرقاقات",
    sectionType: "عناصر النص",
    sectionCard: "البطاقة",
    cardKicker: "قسم توضيحي",
    cardTitle: "عنوان بطاقة توضيحي",
    cardBody: "هيدا نص توضيحي بيبيّن مكان محتوى البطاقة.",
    cardMeta: "سطر معلومات إضافية",
    sectionStats: "شبكة الإحصائيات",
    sectionTable: "جدول",
    tableCol1: "الاسم",
    tableCol2: "النوع",
    tableCol3: "ملاحظة",
    sectionFields: "حقول النموذج",
    fieldLabel1: "حقل نص توضيحي",
    fieldPlaceholder1: "اكتب شي…",
    fieldLabel2: "حقل متعدد الأسطر",
    fieldPlaceholder2: "نص أطول هون…",
    segOptions: ["خيار أ", "خيار ب", "خيار ج"],
    radioLabel: "خيار توضيحي",
    sectionAvatar: "الصورة الرمزية",
    sectionDrawer: "الدرج الجانبي",
    openDrawer: "افتح الدرج",
    drawerTitle: "عنوان درج توضيحي",
    drawerBody: "محتوى توضيحي جوّا الدرج.",
    drawerClose: "إغلاق",
    sectionDialog: "نافذة الحوار",
    openDialog: "افتح الحوار",
    dialogTitle: "عنوان حوار توضيحي",
    dialogBody: "محتوى توضيحي جوّا الحوار.",
    dialogCancel: "إلغاء",
    dialogConfirm: "تأكيد",
    sectionEmpty: "حالة فارغة",
    emptyTitle: "ما في شي هون بعد",
    emptyDescription: "وصف توضيحي لحالة فارغة.",
    emptyAction: "إجراء توضيحي",
    sectionSkeleton: "هيكل التحميل",
    sectionToast: "إشعار عابر",
    pushToast: "أظهر إشعار",
    toastMessage: "هيدا نص إشعار توضيحي.",
    toastAction: "تراجع",
  },
};

export default function ThemePreviewPage() {
  return (
    <div style={{ height: "100vh" }}>
      <IsolatedFrame>
        <ThemePreviewContent />
      </IsolatedFrame>
    </div>
  );
}

function ThemePreviewContent() {
  const [locale, setLocale] = useState("en");
  const [activeNav, setActiveNav] = useState("overview");
  const [segValue, setSegValue] = useState(null);
  const [radioChecked, setRadioChecked] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const { toasts, push, dismiss } = useToasts();

  const t = COPY[locale];
  const navItems = locale === "ar" ? NAV_ITEMS_AR : NAV_ITEMS_EN;
  const statCells = locale === "ar" ? STAT_CELLS_AR : STAT_CELLS_EN;
  const tableRows = locale === "ar" ? TABLE_ROWS_AR : TABLE_ROWS_EN;
  const segOptions = t.segOptions;

  return (
    <Shell
      dir={t.dir}
      lang={t.lang}
      rail={navItems.map((item) => (
        <RailLink
          key={item.key}
          label={item.label}
          badge={item.badge}
          active={activeNav === item.key}
          onSelect={() => setActiveNav(item.key)}
        />
      ))}
      topBar={
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 16px", minHeight: 54, borderBottom: "1px solid var(--tz-color-divider)" }}>
          <Kicker>{t.pageTitle}</Kicker>
          <Button variant="secondary" onClick={() => setLocale(locale === "ar" ? "en" : "ar")}>
            {t.switchLabel}
          </Button>
        </div>
      }
    >
      <div style={{ padding: "var(--tz-space-6) var(--tz-space-8)", display: "flex", flexDirection: "column", gap: "var(--tz-space-6)" }}>
        <div>
          <h1 style={{ margin: 0, fontFamily: "var(--tz-font-heading)", fontWeight: 400 }}>{t.pageTitle}</h1>
          <p className="tz-text-muted" style={{ marginTop: 4 }}>{t.pageSubtitle}</p>
        </div>

        <section>
          <Kicker>{t.sectionButtons}</Kicker>
          <div style={{ display: "flex", gap: "var(--tz-space-2)", marginTop: "var(--tz-space-2)", flexWrap: "wrap", alignItems: "center" }}>
            <Button variant="primary">Primary</Button>
            <Button variant="secondary">Secondary</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="primary" disabled>Disabled</Button>
            <IconButton icon={<span aria-hidden="true">＋</span>} label="Sample icon button" />
          </div>
        </section>

        <Divider />

        <section>
          <Kicker>{t.sectionTags}</Kicker>
          <div style={{ display: "flex", gap: "var(--tz-space-2)", marginTop: "var(--tz-space-2)", flexWrap: "wrap", alignItems: "center" }}>
            <Tag tone="neutral">Neutral</Tag>
            <Tag tone="outline">Outline</Tag>
            <Tag tone="accent">Accent</Tag>
            <Tag tone="accent-2">Accent 2</Tag>
            <Chip active={segValue === "chip-on"} onClick={() => setSegValue(segValue === "chip-on" ? null : "chip-on")}>
              Toggle chip
            </Chip>
          </div>
        </section>

        <Divider />

        <section>
          <Kicker>{t.sectionType}</Kicker>
          <div style={{ display: "flex", gap: "var(--tz-space-4)", marginTop: "var(--tz-space-2)", alignItems: "baseline", flexWrap: "wrap" }}>
            <Figure>128</Figure>
            <Num>1,024</Num>
          </div>
        </section>

        <Divider />

        <section>
          <Kicker>{t.sectionCard}</Kicker>
          <div style={{ marginTop: "var(--tz-space-2)", maxWidth: 360 }}>
            <Card kicker={t.cardKicker} title={t.cardTitle} body={t.cardBody} meta={t.cardMeta} />
          </div>
        </section>

        <Divider />

        <section>
          <Kicker>{t.sectionStats}</Kicker>
          <div style={{ marginTop: "var(--tz-space-2)" }}>
            <StatGrid cells={statCells} />
          </div>
        </section>

        <Divider />

        <section>
          <Kicker>{t.sectionTable}</Kicker>
          <div style={{ marginTop: "var(--tz-space-2)" }}>
            <Table columns={[t.tableCol1, t.tableCol2, t.tableCol3]} rows={tableRows} />
          </div>
        </section>

        <Divider />

        <section>
          <Kicker>{t.sectionFields}</Kicker>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "var(--tz-space-4)", marginTop: "var(--tz-space-2)", maxWidth: 640 }}>
            <Field label={t.fieldLabel1}>
              <Input placeholder={t.fieldPlaceholder1} />
            </Field>
            <Field label={t.fieldLabel2}>
              <Input multiline placeholder={t.fieldPlaceholder2} />
            </Field>
            <Field label="Segmented">
              <Segmented options={segOptions} value={segValue} onChange={setSegValue} />
            </Field>
            <Field>
              <Radio checked={radioChecked} onChange={() => setRadioChecked((value) => !value)} label={t.radioLabel} />
            </Field>
          </div>
        </section>

        <Divider />

        <section>
          <Kicker>{t.sectionAvatar}</Kicker>
          <div style={{ display: "flex", gap: "var(--tz-space-3)", marginTop: "var(--tz-space-2)", alignItems: "center" }}>
            <Avatar initial="A" size="sm" />
            <Avatar initial="B" size="md" accent />
            <Avatar initial="C" size="lg" />
          </div>
        </section>

        <Divider />

        <section>
          <Kicker>{t.sectionDrawer}</Kicker>
          <div style={{ marginTop: "var(--tz-space-2)" }}>
            <Button variant="secondary" onClick={() => setDrawerOpen(true)}>{t.openDrawer}</Button>
          </div>
          <Drawer
            open={drawerOpen}
            onClose={() => setDrawerOpen(false)}
            title={t.drawerTitle}
            closeLabel={t.drawerClose}
            footer={<Button variant="secondary" onClick={() => setDrawerOpen(false)}>{t.drawerClose}</Button>}
          >
            <p>{t.drawerBody}</p>
          </Drawer>
        </section>

        <Divider />

        <section>
          <Kicker>{t.sectionDialog}</Kicker>
          <div style={{ marginTop: "var(--tz-space-2)" }}>
            <Button variant="secondary" onClick={() => setDialogOpen(true)}>{t.openDialog}</Button>
          </div>
          <Dialog
            open={dialogOpen}
            onClose={() => setDialogOpen(false)}
            title={t.dialogTitle}
            actions={(
              <>
                <Button variant="secondary" onClick={() => setDialogOpen(false)}>{t.dialogCancel}</Button>
                <Button variant="primary" onClick={() => setDialogOpen(false)}>{t.dialogConfirm}</Button>
              </>
            )}
          >
            <p>{t.dialogBody}</p>
          </Dialog>
        </section>

        <Divider />

        <section>
          <Kicker>{t.sectionEmpty}</Kicker>
          <div style={{ marginTop: "var(--tz-space-2)", maxWidth: 360, border: "1px solid var(--tz-color-divider)", borderRadius: "var(--tz-radius-md)" }}>
            <EmptyState
              title={t.emptyTitle}
              description={t.emptyDescription}
              actionLabel={t.emptyAction}
              onAction={() => {}}
            />
          </div>
        </section>

        <Divider />

        <section>
          <Kicker>{t.sectionSkeleton}</Kicker>
          <div style={{ marginTop: "var(--tz-space-2)", display: "flex", flexDirection: "column", gap: "var(--tz-space-3)" }}>
            <div style={{ display: "flex", gap: "var(--tz-space-3)", alignItems: "center" }}>
              <Skeleton shape="circle" width="36px" height="36px" />
              <Skeleton shape="text" width="220px" />
            </div>
            <SkeletonRows count={3} />
          </div>
        </section>

        <Divider />

        <section>
          <Kicker>{t.sectionToast}</Kicker>
          <div style={{ marginTop: "var(--tz-space-2)" }}>
            <Button
              variant="secondary"
              onClick={() => push({ message: t.toastMessage, tone: "accent", actionLabel: t.toastAction, onAction: () => {} })}
            >
              {t.pushToast}
            </Button>
          </div>
        </section>
      </div>

      <ToastStack toasts={toasts} onDismiss={dismiss} />
    </Shell>
  );
}
