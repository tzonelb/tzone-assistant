# tzone-theme (v2) — الثيم كحزمة JavaScript لمشروع Vite + React

نسخة معدّلة حسب البنود الستة. **كل صنف CSS صار مسبوق بـ `tz-`**، الألوان موحّدة،
الخطوط تدعم العربي، والـ CSS ملفات حقيقية.

---

## التركيب

انسخ مجلد `tzone-theme/` إلى `src/` بمشروعك، وبعدين بملف الدخول:

```js
// src/main.jsx
import './tzone-theme/tokens.css';   // الترتيب إلزامي
import './tzone-theme/theme.css';
```

React:

```jsx
import { Shell, Button, Drawer, Avatar, EmptyState } from './tzone-theme/react.jsx';
```

---

## ١) سابقة `tz-` — جدول التحويل

| قديم | جديد |
|---|---|
| `.btn` `.btn-primary` `.btn-secondary` `.btn-ghost` `.btn-icon` `.btn-block` | `.tz-btn` `.tz-btn-primary` `.tz-btn-secondary` `.tz-btn-ghost` `.tz-btn-icon` `.tz-btn-block` |
| `.card` `.card-title` `.card-body` `.card-kicker` `.card-meta` | `.tz-card` `.tz-card-title` `.tz-card-body` `.tz-card-kicker` `.tz-card-meta` |
| `.input` `.field` `.radio` | `.tz-input` `.tz-field` `.tz-radio` |
| `.table` | `.tz-table` |
| `.tag` `.tag-neutral` `.tag-accent` `.tag-accent-2` `.tag-outline` | `.tz-tag` `.tz-tag-neutral` `.tz-tag-accent` `.tz-tag-accent-2` `.tz-tag-outline` |
| `.nav` `.nav-brand` | `.tz-nav` `.tz-nav-brand` |
| `.hr` | `.tz-hr` |
| `.plate` | `.tz-plate` |
| `.seg` `.seg-opt` | `.tz-seg` `.tz-seg-opt` (+ `.tz-seg-opt-on` للمختار) |
| `.dialog` `.dialog-backdrop` `.dialog-title` `.dialog-body` `.dialog-actions` | `.tz-dialog` `.tz-dialog-backdrop` `.tz-dialog-title` `.tz-dialog-body` `.tz-dialog-actions` |
| `.elev-sm` `.elev-md` `.elev-lg` | `.tz-elev-sm` `.tz-elev-md` `.tz-elev-lg` |
| `.text-muted` | `.tz-text-muted` |

الأصناف اللي أصلاً `tz-*` بقيت متل ما هي: `tz-kick` `tz-fig` `tz-num` `tz-row`
`tz-chip` `tz-chip-on` `tz-stat` `tz-screen` `tz-tablewrap` `tz-aside`
`tz-rail-link` `tz-mark` `tz-min` `tz-hide` `tz-hidden` `tz-pane-aux`
`tz-conv-list` `tz-avatar`.

كذلك **كل متغيّرات CSS صارت `--tz-*`** (`--tz-color-accent`, `--tz-space-4`,
`--tz-radius-md`, `--tz-font-heading`…) لتفادي التضارب مع متغيّرات مشروعك.

---

## ٢) العربي و RTL

```
--tz-font-heading: "Cormorant Garamond", "Noto Naskh Arabic", system-ui, serif;
--tz-font-body:    "Lora", "Noto Naskh Arabic", system-ui, serif;
```

`Noto Naskh Arabic` محمّل مع باقي الخطوط عبر `GOOGLE_FONTS_HREF` (و`page.js`
و`injectFonts()`). الطابع Naskh فبيمشي مع طابع Cormorant/Lora.

**التنفيذ**: التصميم مبني على *logical properties* — `padding-inline`،
`border-inline-start`، `inset-inline-end`، `text-align: start` — فالاتجاه
بينقلب لحاله. كتلة `[dir="rtl"]` تعالج بس اللي ما بينقلب لحاله:

| القاعدة | ليش |
|---|---|
| `[dir=rtl] .tz-drawer` animation | الـ Drawer بيزحف من الجهة الصح |
| `[dir=rtl] h1..h6`, `[lang=ar] h1..h4` | leading أوسع (1.35) و`letter-spacing: 0` — Naskh بيحتاج مسافة |
| `[dir=rtl] .tz-kick` | ممنوع `letter-spacing` و`uppercase` على العربي — بيفكّ الوصلات |
| `[dir=rtl] .tz-num`, `.tz-fig` | `direction: ltr` فالأرقام تبقى LTR جوّا نص عربي |
| `.tz-flip` | يقلب الأسهم والشيفرونات |

اشتغال ثنائي اللغة: حطّ `dir="rtl" lang="ar"` على `ThemeProvider` أو `Shell`،
واستعمل `dir="auto"` على كل نص جايي من المستخدم (`Input` عامله default).

---

## ٣) توحيد الألوان

التناقض انحلّ. **`tokens.js` هو المصدر الوحيد**:

```
accent   = #1b9be0
accent-2 = #3fb552
```

السلالم `100..900` مولّدة برمجياً من هالقيمتين بـ `ramp()` — تفتيح باتجاه الأبيض
للـ 100–400، و`500` هو الأساس نفسه، وتغميق باتجاه `#06131b` للـ 600–900:

| step | accent | accent-2 |
|---|---|---|
| 100 | `#e8f5fc` | `#ecf8ee` |
| 200 | `#c8e7f8` | `#d1edd5` |
| 300 | `#98d2f1` | `#a9deb1` |
| 400 | `#5bb7e9` | `#75ca82` |
| 500 | `#1b9be0` | `#3fb552` |
| 600 | `#167db5` | `#329146` |
| 700 | `#12628d` | `#27713b` |
| 800 | `#0e496a` | `#1d5431` |
| 900 | `#0b354c` | `#143c29` |

`css.js` ما بقى يعرّف `:root` أبداً — `tokens.css` مولّد من `rootVars()`
بـ `node tzone-theme/build.js`.

---

## ٤) الـ CSS ملفات حقيقية

| ملف | الدور |
|---|---|
| `tokens.css` | `:root` بكل المتغيّرات — **مولّد**، لا تعدّله بالإيد |
| `theme.css` | كل الكومبوننتات + RTL — مكتوب بالإيد، بس بلا أي قيمة حرفية |

الطريقة الأساسية هي `import` مباشر (فوق). `css.js` بقي للتوافق فقط: بيقرا
**نفس** الملفين عبر `?inline` تبع Vite، فما في نسخة تانية من الـ CSS ولا `:root`
مكرّر. `injectStyles()` بس للصفحات بلا bundler أو للـ SSR.

---

## ٥) قاعدة الكومبوننتات

- ولا كومبوننت فيه نص أو رقم أو بيانات ثابتة — كلشي `props`.
- الأسماء `PascalCase`، الـ props `camelCase`.
- ولا اسم حقل قاعدة بيانات: `label` `value` `title` `description` `message`
  `initial` `badge` — مش `name` `qty` `created_at`.
- ولا `hex` أو `px` جوّا كومبوننت — الأصناف بتقرا من `tokens.css`.

---

## ٦) جدول الكومبوننتات (`react.jsx`)

### الإطار

| كومبوننت | prop | نوع | افتراضي | الدور |
|---|---|---|---|---|
| **ThemeProvider** | `theme` | `object` | `{}` | تعديلات runtime لكل tenant |
| | `dir` | `'ltr' \| 'rtl'` | — | اتجاه الواجهة |
| | `lang` | `string` | — | لغة المستند |
| | `className` | `string` | — | |
| | `style` | `object` | — | |
| | `children` | `ReactNode` | — | |
| **Shell** | `rail` | `ReactNode` | — | محتوى الشريط الجانبي |
| | `topBar` | `ReactNode` | — | الشريط العلوي |
| | `theme` | `object` | — | |
| | `dir` | `'ltr' \| 'rtl'` | — | |
| | `lang` | `string` | — | |
| | `railMinimised` | `boolean` | `false` | الشريط مضموم لـ 58px |
| | `children` | `ReactNode` | — | الشاشة |
| **Rail** | `minimised` | `boolean` | `false` | |
| | `children` | `ReactNode` | — | |
| **RailLink** | `label` | `ReactNode` | — | النص |
| | `icon` | `ReactNode` | — | أيقونة 15px |
| | `active` | `boolean` | `false` | يرسم علامة الجهة |
| | `badge` | `ReactNode` | — | رقم على اليمين |
| | `onSelect` | `Function` | — | |
| | `href` | `string` | — | لو موجود بيصير `<a>` |

### أزرار وعناصر صغيرة

| كومبوننت | prop | نوع | افتراضي |
|---|---|---|---|
| **Button** | `variant` | `'primary' \| 'secondary' \| 'ghost'` | `'secondary'` |
| | `block` | `boolean` | `false` |
| | `disabled` | `boolean` | `false` |
| | `onClick` | `Function` | — |
| | `children` | `ReactNode` | — |
| **IconButton** | `icon` | `ReactNode` | — |
| | `label` | `string` | — (إلزامي للـ a11y) |
| | `variant` | `string` | `'secondary'` |
| **Tag** | `tone` | `'neutral' \| 'outline' \| 'accent' \| 'accent-2'` | `'neutral'` |
| | `children` | `ReactNode` | — |
| **Chip** | `active` | `boolean` | `false` |
| | `onClick` | `Function` | — |
| | `children` | `ReactNode` | — |
| **Kicker** | `children` | `ReactNode` | — |
| **Figure** | `children` | `ReactNode` | — |
| **Num** | `children` | `ReactNode` | — |
| **Divider** | `className` | `string` | — |

### بيانات

| كومبوننت | prop | نوع | افتراضي |
|---|---|---|---|
| **Card** | `kicker` | `ReactNode` | — |
| | `title` | `ReactNode` | — |
| | `body` | `ReactNode` | — |
| | `meta` | `ReactNode` | — |
| | `children` | `ReactNode` | — |
| **Stat** | `label` | `ReactNode` | — |
| | `value` | `ReactNode` | — |
| | `note` | `ReactNode` | — |
| **StatGrid** | `cells` | `Array<{label, value, note?}>` | `[]` |
| **Table** | `columns` | `Array<ReactNode>` | `[]` |
| | `rows` | `Array<Array<ReactNode>>` | `[]` |
| | `rowKey` | `(row, index) => string` | index |

### حقول

| كومبوننت | prop | نوع | افتراضي |
|---|---|---|---|
| **Field** | `label` | `ReactNode` | — |
| | `children` | `ReactNode` | — |
| **Input** | `multiline` | `boolean` | `false` |
| | `dir` | `string` | `'auto'` |
| | `value` `onChange` `placeholder` `type` | native | — |
| **Segmented** | `options` | `string[]` | `[]` |
| | `value` | `string` | — |
| | `onChange` | `(value) => void` | — |
| **Radio** | `checked` | `boolean` | `false` |
| | `onChange` | `Function` | — |
| | `label` | `ReactNode` | — |

### الكومبوننتات الجديدة

| كومبوننت | prop | نوع | افتراضي | ملاحظات |
|---|---|---|---|---|
| **Avatar** | `initial` | `string` | — | حرف واحد |
| | `src` | `string` | — | صورة، بتغلب `initial` |
| | `alt` | `string` | — | مع `src` |
| | `size` | `'sm' \| 'md' \| 'lg'` | `'md'` | 26 / 36 / 52px |
| | `accent` | `boolean` | `false` | إطار ونص بلون العلامة |
| **Drawer** | `open` | `boolean` | `false` | |
| | `onClose` | `Function` | — | ESC + ضغط على الـ backdrop |
| | `title` | `ReactNode` | — | يصير `aria-label` |
| | `side` | `'start' \| 'end'` | `'end'` | منطقي — بينقلب مع RTL |
| | `closeLabel` | `string` | — | a11y لزرّ الإغلاق |
| | `footer` | `ReactNode` | — | |
| | `children` | `ReactNode` | — | جسم قابل للتمرير |
| **Dialog** | `open` | `boolean` | `false` | |
| | `onClose` | `Function` | — | ESC + backdrop |
| | `title` | `ReactNode` | — | |
| | `actions` | `ReactNode` | — | أزرار أسفل |
| | `children` | `ReactNode` | — | |
| **EmptyState** | `icon` | `ReactNode` | — | ≤ 24–30px |
| | `title` | `ReactNode` | — | |
| | `description` | `ReactNode` | — | |
| | `actionLabel` | `ReactNode` | — | زر واحد بالكتير |
| | `onAction` | `Function` | — | |
| **Skeleton** | `width` | `string` | — | أي طول CSS |
| | `height` | `string` | — | |
| | `shape` | `'text' \| 'block' \| 'circle'` | `'block'` | |
| **SkeletonRows** | `count` | `number` | `5` | صفوف قائمة |
| | `avatarSize` | `string` | `'36px'` | |
| **Toast** | `message` | `ReactNode` | — | |
| | `tone` | `'neutral' \| 'accent' \| 'success'` | `'neutral'` | ما في أحمر بالنظام |
| | `actionLabel` | `ReactNode` | — | |
| | `onAction` | `Function` | — | |
| | `onDismiss` | `Function` | — | |
| **ToastStack** | `toasts` | `Array<{id, message, tone?, actionLabel?, onAction?}>` | `[]` | |
| | `onDismiss` | `(id) => void` | — | |
| **useToasts** | `options.timeout` | `number` | `6000` | يرجّع `{ toasts, push, dismiss }` |

---

## أمثلة

```jsx
const { toasts, push, dismiss } = useToasts();
const [drawerOpen, setDrawerOpen] = useState(false);

<Shell dir="rtl" lang="ar" rail={<RailLink label="المحادثات" active badge="37" />}>
  <StatGrid cells={[
    { label: 'محادثات مفتوحة', value: '37' },
    { label: 'رد عليها الذكاء', value: '23' },
  ]} />

  <Button variant="primary" onClick={() => setDrawerOpen(true)}>ملف العميل</Button>

  <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)}
    title="العميل والتحكّم" closeLabel="إغلاق">
    <Avatar initial="ن" size="lg" accent />
  </Drawer>

  <EmptyState title="ما في محادثات بعد"
    description="وصّل قناة وبتبلّش الرسائل توصل هون."
    actionLabel="وصّل قناة" onAction={connectChannel} />

  <SkeletonRows count={6} />
  <ToastStack toasts={toasts} onDismiss={dismiss} />
</Shell>
```

---

## الملفات

| ملف | الدور |
|---|---|
| `tokens.js` | المصدر الوحيد: `ramp()`, `COLORS`, `FONTS`, `SPACE`, `RADIUS`, `SHADOW`, `rootVars()`, `runtimeVars()`, `runtimeStyle()`, `cssVar()` |
| `tokens.css` | **مولّد** من `tokens.js` |
| `theme.css` | كل الكومبوننتات + RTL |
| `react.jsx` | كومبوننتات React (الجدول فوق) |
| `ui.js` | نفس الكومبوننتات كـ HTML strings، لـ SSR أو host غير React |
| `page.js` | `head()` و`shell()` لصفحة كاملة |
| `css.js` | توافق فقط: `stylesheet()`, `injectFonts()`, `injectStyles()` |
| `express-adapter.js` | يخدم `/tz/tokens.css` و`/tz/theme.css` |
| `build.js` | `node build.js` يولّد `tokens.css` · `node build.js public` ينسخهم |
