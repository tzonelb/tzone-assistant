# T-ZONE — UI Redesign Implementation Order

For Claude Code, working in `C:\PROJECTS\tzone-assistant`.
The approved design is the mockup `T-ZONE Platform Redesign.dc.html` (19 screens).
Read `SUPER_ADMIN_UI_CONTROL.md` and `CLAUDE_CODE_THEME_SPEC.md` first — the Theme Studio spec is part of this work, not separate.

## 0. Non-negotiables

- Backend stays the source of truth for permissions; theme and layout are presentation only.
- AI stops replying the moment a human owns a conversation; ownership and every action are written to the conversation timeline.
- Channels connect by provider sign-in only — never a token, key or webhook field in the UI.
- Never modify the Messenger webhook path.
- No visual regression on Arabic: every screen must survive `dir="rtl"`.

## 1. Order of work

1. **Design tokens + ThemeProvider** (per `CLAUDE_CODE_THEME_SPEC.md` §4). Nothing else starts until components read CSS variables instead of hard-coded values.
2. **App shell** — top bar, collapsible rail, content area.
3. **Conversations** — the screen with the most change.
4. **Theme Studio** under Platform Admin.
5. The remaining screens, in the order listed in §5.

Ship each step behind a feature flag (`ui_v2`) so the old UI stays reachable until QA signs off.

## 2. App shell

- **Top bar** — logo (`tzone-logo.png`), branch label, global search with ⌘K, live indicator, notification bell with count, user block (name + role). Height is a token (`--tz-topbar`).
- **Side rail** — grouped: Desk (Dashboard, Conversations, Notification Center, Tasks, Appointments, Team Chat) · Customers (Customers, Broadcast, Calls) · Intelligence (AI Teaching, Saved Replies, Reply Flows) · Growth (Community, Master Catalogue, Analytics) · Administration (Company Settings, Roles & Permissions, Settings, Platform Admin, Theme Studio).
- Rail collapses to 58px via a toggle at its top and **auto-expands on hover**; collapsed state persists per user. When collapsed, group headings are removed from flow (not just faded) so icon order never shifts.
- Rail background is the palette's own deep tone (`--tz-rail`), not black; label colour is `--tz-rail-text`.
- Menu entries are filtered by the module visibility config; a hidden module must also 404 on its route.

## 3. Conversations — required behaviour

Layout is **one screen, no page scroll**: the shell fills the viewport; only the thread list, the message area and the details drawer scroll vertically. No horizontal scrollbar at any width.

- **Filter row** under the top bar: a Folders dropdown (Inbox · Assigned to me · Unread · Starred · Done · Archived, each with a count), a Channels dropdown, and "New conversation". No chip bar, no duplicate "Inbox" heading, no sort control (sort is newest-first by default), no "Select" toggle.
- **Thread list** (~330px, shrinks to 230px, hidden below 940px): search field, status chips (Open · Waiting · Snoozed), then rows showing avatar, name, unread dot, time, two-line preview, channel colour chip, owner and an SLA line ("Waiting 13 min", "Unassigned 34 min"). The open row is marked with an accent edge. Multi-select starts by clicking a row — no separate checkbox mode.
- **Conversation column**: header = avatar, online dot when the customer is online, name, meta line, ownership tag, "Return to AI", "Close chat". Clicking the name opens the details drawer. "Close chat" leaves the conversation and shows a "No conversation open" empty state.
- Below the header, the AI-state banner: who owns the chat, when they took it, the confidence that triggered escalation.
- **Message area**: day separators, per-message sender + time + AI confidence, delivery/read receipts, typing indicator.
- **Composer**: one row — text field with attachment / image / emoji actions, a **voice-note record button**, and send. ⌘↵ sends.
- **Details drawer** (320px, overlays the conversation column, opens from the customer name, closes with its own ✕): accordion cards — Timeline · Customer · Conversation control (transfer, change department, snooze, reopen) · Create from this chat (task, appointment, repair ticket, quote) · Export & share (PDF, CSV, share link, email) · Moderation (spam, block). Cards remember their open state; only Timeline is open by default.

## 4. Responsive rules (apply everywhere)

- No element declares a pixel `min-width` wider than its content column. Multi-pane screens shrink their side panes and hide the least important one below a breakpoint.
- Tables scroll inside their own bordered card (`overflow-x:auto`), never by scrolling the page.
- Stat grids reflow with `repeat(auto-fit, minmax(150px, 1fr))`; hairlines are drawn per cell (inset box-shadow), never by painting the container.

## 5. Remaining screens

Build to the mockup, reusing the existing endpoints:

Dashboard · Notification Center · Tasks · Appointments · Team Chat · Customers (+ detail timeline) · Broadcast · Calls · AI Teaching (instructions, knowledge, test bench) · Saved Replies · Reply Flows (canvas + node library from `nodeTypesConfig.js`) · Community/Publish (week scheduler + comment inbox) · Master Catalogue · Analytics · Company Settings/Channels (connected + honest "coming soon" list from `channelCatalog.js`) · Roles & Permissions · Settings · Platform Admin · Theme Studio.

## 6. Acceptance

- Every screen: `document.scrollWidth === clientWidth` at 1280, 1024 and 924 px wide; no element clipped without a scroll container.
- Conversations: composer visible without scrolling at 540px height; drawer opens from the customer name at every width; "Close chat" reaches the empty state and back.
- Rail: collapse persists, hover-expand works, icon order identical collapsed and expanded.
- Theme: changing palette, fonts, radius or density in Theme Studio moves every screen consistently; a tenant override never leaks to another tenant.
- RTL: full pass in Arabic with no overlap or mirrored-icon mistakes.
- Manual QA on two tenants and two roles (owner, agent) before this is called done — a green build is not acceptance.
