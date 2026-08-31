# First-run checklist — من الصفر / prove it works end to end

> بعد ما تصير المنصة LIVE، امشِ على هالقائمة مرة وحدة. كل بند إله **كيف تفحصه**
> و**الناتج الصحيح**. إذا كلهم خضر، المنصة جاهزة لأي مستخدم جديد.
>
> After the platform is live, walk this once. Each item says how to check it and
> what "good" looks like. All green = ready for a real new user.

Replace `bot.tz-lb.com` with your domain. Server commands run on the VPS; browser
steps run in a normal browser (use a private window to test as a fresh user).

---

## A. المنصة حية / The platform is up

| # | Check | Good result |
|---|---|---|
| A1 | `curl -s https://bot.tz-lb.com/health/` | `{"status":"ok",...}` |
| A2 | Open `https://bot.tz-lb.com/` in a browser | the **login page** loads (not JSON, not an error) |
| A3 | `sudo -u tzone bash -c 'set -a; . /etc/tzone/tzone.env; set +a; cd /opt/tzone && ./venv/bin/python -m tools.manage_platform check'` | exits `0`, prints OK |
| A4 | Padlock in the address bar | valid HTTPS (Let's Encrypt), no warning |

---

## B. دخول الشركة / A company owner can log in

| # | Check | Good result |
|---|---|---|
| B1 | Go to `/login`, enter the **workspace code**, owner **email**, **password** | lands on the dashboard |
| B2 | Wrong workspace code | rejected, no hint about which field was wrong |
| B3 | Refresh the page while logged in | still logged in (session cookie works) |
| B4 | Log out | back to `/login`, protected pages no longer open |

---

## C. لوحة التحكم / The Super Admin console works — **this activates the platform**

| # | Check | Good result |
|---|---|---|
| C1 | Go to `/superadmin/login` | the console sign-in page (no workspace-code field) |
| C2 | Sign in with the Super Admin email + password | prompts you to set up 2FA (first login) |
| C3 | Scan the QR with an authenticator app, enter the 6-digit code | enrolled; the console (Companies / Admins / Audit / Health) opens |
| C4 | Sign out and back in — it now asks for the 6-digit code | 2FA is enforced |

If no Super Admin exists yet (older install), create one first:
```bash
cd /opt/tzone
sudo -u tzone bash -c 'set -a; . /etc/tzone/tzone.env; set +a; \
  ./venv/bin/python -m tools.manage_platform create-super-admin \
    --email you@example.com --name "Your Name" --password "at-least-10-chars"'
```

---

## D. تفعيل شركة جديدة / Activate a brand-new company (the core operator flow)

Do this from the console as Super Admin — it is how every customer gets onboarded.

| # | Check | Good result |
|---|---|---|
| D1 | Companies → **New company**: fill name, workspace, owner email + name + password | company created; a **workspace code is shown once** — copy it |
| D2 | (optional) Assign a plan to the new company | plan shows on the company's page |
| D3 | Open a **private browser window** → `/login` → the new workspace code + new owner email/password | the new owner reaches their own empty dashboard |
| D4 | Confirm isolation: the new company sees **none** of the first company's data | separate, empty workspace |
| D5 | Back in the console → suspend the company → try D3 again | login refused while suspended; reactivating restores it |

D3 passing is the whole point: **a person you never touched can log in and use their own workspace.**

---

## E. الاستعمال اليومي / Everyday use inside a company

| # | Check | Good result |
|---|---|---|
| E1 | Users/roles screen: add a teammate, give a role | they can log in with the same workspace code + their own email/password |
| E2 | Try to add more users than the plan allows | rejected with a clear message; the audit log records the attempt |
| E3 | Knowledge base: add an entry, reload | it persists and is readable |
| E4 | Connect a channel (Messenger/WhatsApp/Telegram) per its setup | the channel's webhook verifies; status shows connected |
| E5 | Send a test message to a connected channel | it appears in Conversations; the assistant replies per the company's policy |
| E6 | Audit log screen | shows who did what, when, from which IP for the steps above |

---

## F. الأمان والاستمرارية / Security & continuity

| # | Check | Good result |
|---|---|---|
| F1 | In the browser dev-tools → Application → Storage | **no token in localStorage** (auth is an httpOnly cookie) |
| F2 | Network tab on a company API response (e.g. conversations) | **no colleague emails/phones, no plan price, no channel IDs** leaked |
| F3 | 5 wrong passwords on one account | account locks ~30 min; a real login from elsewhere is unaffected |
| F4 | `ls -la /var/backups/tzone` | nightly backups exist |
| F5 | The **master key** is saved offline (not only on the server) | you have a second copy |
| F6 | `systemctl list-timers tzone-update.timer` | listed and active (auto-deploy is armed) |

---

## G. النشر التلقائي / Push-to-deploy works

| # | Check | Good result |
|---|---|---|
| G1 | Push a trivial commit to the deployed branch | within ~3 min the server updates itself |
| G2 | `tail -n 20 /var/log/tzone-update.log` | shows the pull + restart + "healthy" |
| G3 | (once, before going private) `sudo bash /opt/tzone/deploy/setup-deploy-key.sh`, add the key to GitHub, then `sudo -u tzone git -C /opt/tzone fetch origin` | fetch succeeds with no password — safe to make the repo private |

---

## Automated backstop / الفحص الآلي

Run the whole test suite on any checkout before shipping:
```bash
export TZONE_MASTER_KEY=$(python3 -c "from backend.security.keyring import generate_master_key;print(generate_master_key())")
export DATA_DIR=/tmp/verify && rm -rf $DATA_DIR
python3 -m pytest tests/ -q
```
All tests must pass. This is also enforced in CI on every push.

---

*If any row is red, stop and fix it before onboarding a real customer. The point
of this list is that "everyone can use it" is something you verified, not hoped.*
