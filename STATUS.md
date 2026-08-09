# T-ZONE Platform — Live Status

> **Single source of truth for "where are we / what's done / what's left".**
> Updated and pushed with every batch of work. Resuming from another machine:
> `git checkout fix/release-timeout-and-channel-fixes`
> `git pull origin fix/release-timeout-and-channel-fixes`
> then read this file top to bottom.

**Last updated:** 2026-08-09
**PRIMARY branch:** `fix/release-timeout-and-channel-fixes`  ← this is the real product now
**Latest commit:** `d3c8146`
**Run:** `uvicorn main:app --reload` (root `main.py` is canonical) + `cd frontend && npm run dev`

---

## ⚠️ READ THIS FIRST — the two-branch story (why "features looked missing")

There were **two branches developed in parallel** that never merged:

| Branch | What it is |
|---|---|
| `fix/release-timeout-and-channel-fixes` | **THE PRIMARY / real product.** 83+ commits: full v2 UI overhaul, Theme Studio, Platform Admin, mobile app (Expo), Windows app (Electron), module gating, Reply Flows with trigger types, attachments/voice/image in Conversations, granular permissions, Analytics, Catalogue, Tasks, Appointments, Scheduler, Team Chat, Calls, AI Teaching — **plus everything ported in from the other branch (below).** |
| `claude/tzone-release-timeout-fixes-pesy2r` | A separate parallel branch (Claude cloud sessions). Had its OWN versions of some modules + unique work (webhook security, Dialer, DB self-heal, time-based triggers, a security re-audit). **Now fully ported onto the primary branch.** This branch is superseded — do NOT develop on it anymore. |

**Decision (made 2026-08-09):** primary = `fix/release-timeout-and-channel-fixes`. All the unique work from the other branch was ported over onto it with full test verification. Nothing was lost.

**If the app "looks like it's missing features" on your laptop:** you are running an OLD branch or a STALE browser/dev-server cache. Fix:
```bash
git checkout fix/release-timeout-and-channel-fixes
git pull origin fix/release-timeout-and-channel-fixes
pip install -r requirements.txt
uvicorn main:app --reload
# second terminal:
cd frontend && npm install && npm run dev
# then in the browser: HARD REFRESH (Ctrl+Shift+R) or use an incognito window
```
After pulling you must see commit `d3c8146` at the top of `git log`.

---

## ✅ What was ported/fixed onto the primary branch in the latest sessions

All pushed, all verified (642 backend tests pass; the only 6 "failing" tests are pre-existing and require a real `OPENAI_API_KEY` in the env — not caused by any of this work; frontend build clean).

1. **Webhook security** (`b20823e`) — `channels/meta/verifier.py` was an EMPTY file: the Meta/WhatsApp webhooks accepted any unsigned request from anyone on the internet (fake customer messages → unlimited paid AI usage; real outbound sends). Added real HMAC-SHA256 `X-Hub-Signature-256` verification + a socket-IP sliding-window rate limiter. Enforced when `META_APP_SECRET` is set; dev-only bypass with a loud warning; rejected in production if unconfigured.
2. **DB self-heal** (`2b41c8e`) — when a laptop DB was created by the OTHER branch, its module tables had a different shape and the app crashed on boot (`no such column: ...`). `create_tables()` now renames a wrong-shape table aside to `{table}_wrongshape_backup` (never dropping data) and restores this branch's original table from `{table}_legacy_backup` if present. Covers tasks/appointments/scheduled_posts/team_chat/call_logs/broadcasts/etc.
3. **Time-based Reply Flow triggers** (`391e77b`) — added `customer_no_reply` ("customer went silent N min") and `team_no_reply` ("team hasn't replied N min") to the Reply Flow trigger registry + engine + builder UI. Scanned every ~30s by the existing `reminder_worker`, once-per-period claim-then-act (no double-fire).
4. **Dialer module** (`9af2563`) — real calling behind a Twilio provider abstraction: dial pad, place calls, transfer a live call to an employee, AI auto-answer for inbound (greeting + voicemail + team notification), call recording, signature-verified Twilio webhooks, auto-mirror of finished calls into the Calls log. Page at `/dialer`, `dialer.use` permission. **OFF until credentials are set** (see "Needs your input" below) — shows a setup notice, never crashes.
5. **Round-1 security re-audit** (`d3c8146`) — re-checked the other branch's 23-finding audit against the primary code; 4 still applied here and are now fixed:
   - **Cross-tenant channel takeover** — a company could "connect" a WhatsApp/Messenger/Instagram/Telegram identity already owned by ANOTHER company, silently hijacking that tenant's inbound message routing. Now globally rejected.
   - **Last-admin lockout** — an admin could demote/deactivate the only admin, or strip `users.manage` from the only admin-carrying role, permanently locking the company out of user management. Now guarded.
   - **Ops kill switch** — `automation_policy.is_bot_enabled()` existed but was never checked; a "disabled" channel kept auto-replying via the scripted flow. `Engine.handle()` now returns `None` for a disabled channel and callers handle it.
   - **Unauthenticated `POST /test/whatsapp/`** — a public way to drive the real message engine. Now requires login. Also deleted the orphaned unauthenticated `admin/api/app.py` Flask app (RCE risk).

---

## 🧭 What's in the platform now (primary branch) — sidebar map

Dashboard · Notification Center · Conversations · Publish · Comments · Customers ·
Broadcast · **Calls · Dialer** · Master Catalogue · Test & Train AI · Tasks ·
Saved Replies · Appointments · Analytics · Team Chat · Settings · Company Settings ·
**Platform Admin** (super-admin only).

Some items are **module-gated** (`modules.appointments`, `modules.team_chat`,
`modules.comments`, `modules.catalogue`): a Super Admin sees them all; a regular
company only sees a module if it's enabled for them in Platform Admin / their plan.
**This is by design** — if a feature "doesn't show" for a non-super-admin, check the
module is enabled for that company and the user's role has the matching permission.

---

## ❗ Needs YOUR input / decisions (not code gaps)

1. **Dialer live activation** — create a Twilio account, buy a number, set in `.env`:
   `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, `PUBLIC_BASE_URL`,
   and point the Twilio number's Voice webhook at
   `POST {PUBLIC_BASE_URL}/api/dialer/webhooks/inbound`. Until then the Dialer page
   shows a setup notice and refuses to place calls (clean, no crash).
2. **Production secrets** — `META_APP_SECRET` (webhooks reject unsigned traffic without
   it in production, by design), `TOKEN_ENCRYPTION_KEY` (a real Fernet key), `OPENAI_API_KEY`.
3. **Conversational voice AI** (a full talking AI over the phone, real-time STT/TTS) is a
   separate, larger future build — today the AI answers inbound calls with a spoken
   greeting + voicemail recording.

---

## 🚦 Launch-readiness audit — NOT yet run on the primary branch

The formal gate (5 consecutive full-codebase audit rounds with ZERO confirmed findings)
has **not** been run against `fix/release-timeout-and-channel-fixes` yet. The re-audit
above only re-checked one prior branch's findings. A fresh, full audit of the primary
branch is the recommended next step before launch. **Clean-streak: 0 / 5.**

---

## 📌 "في نقص كبير" — how to report what's missing so it gets fixed fast

If something still looks missing after a clean pull + hard refresh, tell me, for each item:
- **exact page/button** (or sidebar name) that's missing or broken,
- what you **expected** vs what you **see**,
- whether you're logged in as **Super Admin** or a regular employee,
- any **red error** in the browser console (F12 → Console) or the `uvicorn` terminal.

That lets me reproduce and fix precisely instead of guessing. The code for every module
listed above is present on this branch and verified — so "missing" is now almost always
(a) wrong branch, (b) stale build/cache, or (c) a module not enabled for that company.

---

## Resuming from another machine (quick)

1. `git checkout fix/release-timeout-and-channel-fixes && git pull`
2. Read this file + `docs/DECISION_LOG.md`.
3. Run backend + frontend (commands at the top). Hard-refresh the browser.
4. Everything committed here is on GitHub — nothing lives only in a chat session.

---

## 🆕 Session additions (2026-08-09, later batch) — new channels, notifications, hardening

Committed on top of `d3c8146`. 669 backend tests pass; frontend build clean.

**New channels (no Meta developer app required):**
- **WhatsApp via QR pairing** — `channels/whatsapp_qr/` (a small Node "bridge" speaking the
  WhatsApp Web protocol + a Python client/webhook). Company Settings → Channels → "WhatsApp
  (QR scan)": scan from the phone's Linked Devices. Messages flow through the SAME unified
  inbox pipeline as Cloud API WhatsApp. Run the bridge with `start-wa-bridge.bat`
  (first time: `cd channels/whatsapp_qr/bridge && npm install`). Env: `WA_BRIDGE_URL`,
  `WA_BRIDGE_SECRET` (default works locally; set a strong value in production).
- **Instagram direct login** (instagrapi) + **Facebook cookie download** (facebook-scraper)
  feeding the Comments module — `backend/services/social_session_service.py`. IG can reply;
  FB is read-only. "Sync now" button on the Comments page.

**Notification Center expanded** — bell notifications now also fire for: new comments,
post published/failed, task assigned/completed/due, appointment created/reminder (in
addition to customer messages and conversation reminders). Deduped; time-based ones use
`due_notified_at` / `reminder_notified_at` claim markers so they fire once.

**Module gating fixed** — Dialer added to the v2 sidebar; Theme Studio module keys aligned;
per-company module flags now hide sidebar items and enforce on the scheduler route; new
`RequireAccess` route guards so hidden modules aren't reachable by direct URL.

**Hardening (multi-round deep review + concurrency/perf audit):** WhatsApp QR bridge rate
limit + secret hardening + path-traversal guards + fixed reconnect (pairing completes) +
revoke-old-device-on-re-pair; single-shot task completion (no duplicate customer message);
IntegrityError-safe `notification_service.create()` and `connect_whatsapp_qr`; background
worker DB work offloaded via `asyncio.to_thread` (no event-loop stalls under write-lock);
canonical UTC timestamp normalization; bounded due-scan with indexes.

**In progress:** load/stress testing the platform at ~10k+ simultaneous inbound+outbound
messages across ~100 companies to find and fix scaling bottlenecks (SQLite write
contention is the prime suspect).
