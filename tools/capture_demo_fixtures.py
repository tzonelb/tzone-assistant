"""Record the demo preview's fixtures from the real application.

The preview site at /tzone-assistant/ serves the real frontend with no server
behind it. Its data is not written by hand: this script provisions a throwaway
encrypted company, seeds it through the real endpoints, and captures every GET
the screens make, verbatim. That way the preview shows the shapes the API
actually returns -- a screen reading a field the server does not send fails in
the preview exactly as it would in production.

Run it whenever an endpoint's response shape changes:

    python3 tools/capture_demo_fixtures.py

It rewrites frontend/src/demo/fixtures.json. Nothing it touches is persistent:
the company lives in a temporary directory that is discarded on exit.
"""

import json, sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "frontend" / "src" / "demo" / "fixtures.json"

import os, sys, json, tempfile
from pathlib import Path

# Derived, not hard-coded: this has to import the checkout it lives in, or a
# capture run from a second checkout silently records the other one's shapes
# into this one's fixtures.json.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.security import keyring
os.environ.setdefault("TZONE_MASTER_KEY", keyring.generate_master_key())

DATA = Path(tempfile.mkdtemp(prefix="demo-capture-"))
os.environ["DATA_DIR"] = str(DATA)

from database.manager import DatabaseManager, utc_now_iso
import database.manager as manager_module
from database.schema_control import DEFAULT_ROLES

manager = DatabaseManager(data_dir=DATA / "data")
manager_module.database_manager = manager
for mod in list(sys.modules.values()):
    held = getattr(mod, "database_manager", None)
    if isinstance(held, DatabaseManager) and held is not manager:
        setattr(mod, "database_manager", manager)

COMPANY_NAME = "Cedar Home Appliances"
with manager.control() as conn:
    now = utc_now_iso()
    conn.execute("INSERT INTO workspaces (name, slug, status, created_at, updated_at)"
                 " VALUES ('T-ZONE', 'tzone', 'active', ?, ?)", (now, now))
    cur = conn.execute("INSERT INTO companies (workspace_id, name, slug, status, created_at, updated_at)"
                       " VALUES (1, ?, 'cedar-home', 'active', ?, ?)", (COMPANY_NAME, now, now))
    COMPANY_ID = int(cur.lastrowid)
    conn.commit()

CODE = keyring.generate_workspace_code()
manager.provision_company(company_id=COMPANY_ID, workspace_code=CODE)

with manager.control() as conn:
    now = utc_now_iso()
    perm = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM permissions").fetchall()}
    for name, code, description, codes in DEFAULT_ROLES:
        cur = conn.execute("INSERT OR IGNORE INTO roles (company_id, name, code, description, is_system, created_at)"
                           " VALUES (?, ?, ?, ?, 1, ?)", (COMPANY_ID, name, code, description, now))
        rid = cur.lastrowid
        if not rid:
            continue
        conn.executemany("INSERT OR IGNORE INTO role_permissions (role_id, permission_id, created_at) VALUES (?, ?, ?)",
                         [(rid, perm[c], now) for c in codes if c in perm])
    conn.commit()

from backend.services.auth_service import auth_service
EMAIL, PASSWORD = "rana@cedarhome.example", "DemoOwner123!"
USER_ID = auth_service.create_user(email=EMAIL, password=PASSWORD, full_name="Rana Haddad")
with manager.control() as conn:
    role = conn.execute("SELECT id FROM roles WHERE company_id = ? AND code = 'owner'", (COMPANY_ID,)).fetchone()
    conn.execute("INSERT INTO company_users (company_id, user_id, role_id, status, created_at)"
                 " VALUES (?, ?, ?, 'active', ?)", (COMPANY_ID, USER_ID, int(role["id"]), utc_now_iso()))
    conn.commit()

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api.routes import (auth, platform_ui, dashboard, conversations, manual_messages,
                                conversation_tags, saved_replies, customers, knowledge, catalogue,
                                comments, scheduler, appointments, team_chat, notifications,
                                roles, tickets, analytics, ai_teaching, channels, company_settings,
                                activity, broadcasts)

app = FastAPI()
for module in (auth, platform_ui, dashboard, conversations, manual_messages, conversation_tags,
               saved_replies, customers, knowledge, catalogue, comments, scheduler, appointments,
               team_chat, notifications, roles, analytics, ai_teaching, channels,
               company_settings, activity, broadcasts):
    app.include_router(module.router)
app.include_router(tickets.router)
app.include_router(tickets.tasks_router)
# Contacts is two routers: the register and its saved segments.
app.include_router(customers.segments_router)

client = TestClient(app, raise_server_exceptions=False)
r = client.post("/api/auth/login", json={"company": COMPANY_NAME, "email": EMAIL, "password": PASSWORD})
assert r.status_code == 200, r.text


LOGIN_BODY = r.json()
AUTH = {"Authorization": f"Bearer {r.json()['access_token']}", "X-CSRF-Token": r.json().get("csrf_token","")}
from backend.services.conversation_control_service import conversation_control_service
from backend.services.message_service import message_service

# (direction, text, sender_type, sender_user_id, minutes_ago).
#
# `sender_type` and `minutes_ago` matter as much as the words. Every outbound
# message used to be seeded as sender_type "bot" -- a value nothing in the
# platform counts -- and every message was stamped "now". So the preview drew a
# one-day chart, reported a 0% automation rate, and showed an empty employee
# table: not a bug in the report, a seed that never exercised it.
#
# `None` for the user id means the assistant sent it. A number means a person
# did, and it is the id of the employee this script signs in as.
def PEOPLE_FOR(employee_id):
    return [
        ("messenger", "cust-lina", "Lina Khoury", [
            ("in", "مرحبا، بدي اعرف اذا في توصيل عالمنصورية؟", "customer", None, 60 * 24 * 9),
            ("out", "أهلاً لينا! نعم منوصّل عالمنصورية، الرسوم ٣ دولار والتوصيل خلال ٢٤ ساعة.", "ai", None, 60 * 24 * 9 - 1),
            ("in", "تمام، وكم سعر الباقة الشهرية؟", "customer", None, 60 * 24 * 9 - 4),
            ("out", "الباقة الشهرية ٢٥ دولار، وفيها تركيب مجاني.", "employee", employee_id, 60 * 24 * 9 - 22),
        ]),
        ("whatsapp", "cust-omar", "Omar Saad", [
            ("in", "Hi, is the small package still available?", "customer", None, 60 * 24 * 6),
            ("out", "Hello Omar — yes, it is. Would you like me to reserve one for you?", "ai", None, 60 * 24 * 6 - 1),
            ("in", "Yes please, reserve it under my name.", "customer", None, 60 * 24 * 6 - 5),
            ("out", "Done — it is reserved under your name until Friday.", "employee", employee_id, 60 * 24 * 6 - 11),
        ]),
        ("instagram", "cust-nour", "Nour Aoun", [
            ("in", "شفت البوست تبع العرض، لسا شغال؟", "customer", None, 60 * 24 * 4),
            ("out", "أهلاً نور! العرض شغال لآخر الشهر.", "ai", None, 60 * 24 * 4 - 1),
        ]),
        ("telegram", "cust-karim", "Karim Fares", [
            ("in", "Do you have an English catalogue?", "customer", None, 60 * 24 * 2),
            # The one that went badly: two hours before anybody answered. It is
            # here on purpose -- a preview where every wait is instant cannot
            # show what the distribution and the "longest waits" list are for.
            ("out", "We do — sending it over now, sorry for the wait.", "employee", employee_id, 60 * 24 * 2 - 122),
        ]),
        ("messenger", "cust-maya", "Maya Rizk", [
            ("in", "بدي غيّر موعدي من الخميس للسبت", "customer", None, 60 * 20),
            ("out", "أكيد مايا، عدّلتلك الموعد للسبت الساعة ١١ صباحاً.", "ai", None, 60 * 20 - 1),
        ]),
        # Nobody ever replied to Rami. The most important row on the reporting
        # screen is the customer who wrote and got nothing back, and a seed
        # where everyone was answered cannot show it.
        ("whatsapp", "cust-rami", "Rami Daher", [
            ("in", "بعتلكن مبارح وما وصلني جواب، بدي اعرف اذا الطلب جهز", "customer", None, 60 * 30),
        ]),
    ]


PEOPLE = PEOPLE_FOR(USER_ID)

# Seeded through the real writer, then backdated. `save_message` stamps
# `created_at` with the clock, which is right for a live message and useless
# for a demo: a reporting screen whose every message landed in the same minute
# has nothing to draw. Only the timestamp is rewritten -- the row itself, and
# every other column on it, is the one the platform wrote.
from datetime import datetime, timedelta, timezone

def _minutes_ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()

# `sender_type` is not decoration: reporting counts 'ai' as an assistant reply
# and 'employee' as a person's, and nothing in this platform ever writes 'bot'
# (channels/meta/smart_reply.py writes "ai", manual_messages.py writes
# "employee"). Seeding "bot" made every reply invisible to
# analytics_service.overview, so the recorded summary said the assistant had
# answered nothing and the automation rate was 0% while five conversations sat
# there answered. The last conversation is answered by a person, so the
# employee side of the report is not empty either.
HUMAN_ANSWERED = {"cust-maya"}

for channel, ext, name, msgs in PEOPLE:
    conversation_control_service.get_or_create(company_id=COMPANY_ID, channel=channel, external_user_id=ext)
    for direction, text, sender_type, sender_user_id, minutes_ago in msgs:
        saved = message_service.save_message(
            company_id=COMPANY_ID, channel=channel, external_user_id=ext,
            direction=direction, text=text,
            sender_type=sender_type, sender_user_id=sender_user_id,
        )
        with manager.tenant(COMPANY_ID) as conn:
            conn.execute("UPDATE messages SET created_at = ? WHERE id = ?",
                         (_minutes_ago(minutes_ago), saved["id"]))
            conn.commit()

# ---- a month of history, so the report has a shape ----------------------
# save_message always stamps "now", which is right everywhere except here: a
# capture taken in one second gives the reporting screen a single day and one
# hour, and its volume chart draws a lone point that reads as a broken chart
# rather than a quiet company. These are real rows written through the real
# service; only their created_at is moved back across the 30-day window the
# screen defaults to. Nothing else backdates, and nothing invented: each one is
# a message that exists, on a conversation that exists.
import random
from datetime import datetime, timedelta, timezone as _tz

HISTORY = [
    ("in", "customer", "مرحبا، بدي استفسر عن الباقة"),
    ("out", "ai", "أهلاً فيك! الباقة الشهرية ٤٥ دولار وبتشمل التوصيل المجاني داخل بيروت."),
    ("in", "customer", "Do you deliver outside Beirut?"),
    ("out", "ai", "Yes — $3 outside Beirut, arriving within 24 hours."),
    ("in", "customer", "شكراً، بفكر فيها"),
    ("out", "employee", "تكرم عينك، وقت ما بتجهز خبرنا."),
]

random.seed(20260830)  # a fixed shape, so re-running does not reshuffle the chart
backdated = []

for day_offset in range(1, 29):
    channel, ext, _name, _msgs = PEOPLE[day_offset % len(PEOPLE)]
    when = datetime.now(_tz.utc) - timedelta(days=day_offset)

    # One exchange per day, starting at a business hour and running forward a
    # few minutes per message. The order matters: first_response_times measures
    # the first inbound against the first outbound *after* it, so scattering the
    # times randomly would have reported a customer waiting a day and a half for
    # a reply that was actually written minutes later.
    opened_at = when.replace(
        hour=random.choice([9, 10, 11, 12, 14, 15, 16, 17, 18, 20, 21]),
        minute=random.randrange(45), second=0, microsecond=0,
    )

    for step, (direction, sender_type, text) in enumerate(
        HISTORY[: 2 + (day_offset % 3) * 2]
    ):
        saved = message_service.save_message(
            company_id=COMPANY_ID, channel=channel, external_user_id=ext,
            direction=direction, text=text, sender_type=sender_type,
            sender_user_id=USER_ID if sender_type == "employee" else None,
        )
        stamp = opened_at + timedelta(minutes=step * random.randrange(2, 7))
        backdated.append((stamp.isoformat(), int(saved["id"])))

with manager.tenant(COMPANY_ID) as conn:
    conn.executemany("UPDATE messages SET created_at = ? WHERE id = ?", backdated)
    conn.commit()

# ---- the assistant's own diagnostics -------------------------------------
# "Assistant health" on the reporting screen is not counted from messages: it
# reads diagnostic_events, which only the live OpenAI path writes
# (channels/meta/smart_reply.py). A capture cannot call OpenAI, so that card
# recorded as all zeros -- a screen saying the assistant had sent nothing and
# failed nothing, next to a report of the replies it sent. These go through
# diagnostics_service with the exact event names and fields smart_reply.py
# uses, one per assistant reply that was actually seeded, so the card reports
# the same replies the rest of the screen does.
from backend.services.diagnostics_service import diagnostics_service

with manager.tenant(COMPANY_ID) as conn:
    AI_REPLIES = [
        (row["channel"], row["external_user_id"])
        for row in conn.execute(
            "SELECT channel, external_user_id FROM messages WHERE sender_type = 'ai'"
        ).fetchall()
    ]

for _index, (_channel, _ext) in enumerate(AI_REPLIES):
    diagnostics_service.record(
        event_type="ai_reply_sent", company_id=COMPANY_ID, channel=_channel,
        external_user_id=_ext, status="sent",
        duration_ms=1800 + random.randrange(2600),
        data={"message_count": 1, "batched": False},
    )

# Two failed attempts and one wait-for-a-human timeout: a preview where nothing
# ever fails teaches an owner nothing about the screen that exists to show it.
for _channel, _ext in (("whatsapp", "cust-omar"), ("telegram", "cust-karim")):
    diagnostics_service.record(
        event_type="ai_reply_error", company_id=COMPANY_ID, channel=_channel,
        external_user_id=_ext, severity="error", status="failed",
        data={"error": "Upstream timed out"},
    )

diagnostics_service.record(
    event_type="ai_buffer_waiting_for_human_timeout", company_id=COMPANY_ID,
    channel="messenger", external_user_id="cust-maya", severity="warning",
    data={"waited_seconds": 900},
)

def post(path, body):
    r = client.post(path, json=body, headers=AUTH)
    if r.status_code >= 400:
        print("SEED FAIL", path, r.status_code, r.text[:200], file=sys.stderr)
    return r

# departments first: conversations reference them by code
for code, ar, en in (("sales","المبيعات","Sales"),("support","الدعم","Support"),("delivery","التوصيل","Delivery")):
    post("/api/ai-teaching/departments", {"code":code,"name_ar":ar,"name_en":en,"enabled":True})

CHANNEL_SEED = [
    ("messenger", "Cedar Home",      {"page_id": "104857600011"}),
    ("whatsapp",  "Cedar Home Sales",  {"phone_number_id": "159357456123"}),
    ("instagram", "cedarhome.lb", {"instagram_business_id": "178899220033"}),
    ("telegram",  "Cedar Home Bot",  {"access_token": "7654321098:AAHdemoBotTokenForThePreviewSite01234"}),
]
for ch, nm, ids in CHANNEL_SEED:
    post("/api/channels", {"channel":ch, "name":nm, "ai_enabled":True, **ids})

# real names, sections and flags on the conversations
CONTROL = {
    ("messenger","cust-lina"):  {"customer_alias":"لينا خوري","department":"sales","priority":"high","is_starred":True},
    ("whatsapp","cust-omar"):   {"customer_alias":"Omar Saad","department":"sales","is_pinned":True},
    ("instagram","cust-nour"):  {"customer_alias":"نور عون","department":"support"},
    ("telegram","cust-karim"):  {"customer_alias":"Karim Fares","department":"support"},
    ("messenger","cust-maya"):  {"customer_alias":"مايا رزق","department":"delivery","priority":"normal"},
}
for (ch, ext), body in CONTROL.items():
    rr = client.patch(f"/conversations/{ch}/{ext}/control", json=body, headers=AUTH)
    if rr.status_code >= 400:
        print("CONTROL FAIL", ch, ext, rr.status_code, rr.text[:160], file=sys.stderr)

for ch, ext in (("telegram", "cust-karim"), ("whatsapp", "cust-omar")):
    rr = client.post(f"/conversations/{ch}/{ext}/take-over", headers=AUTH)
    if rr.status_code >= 400:
        print("TAKEOVER FAIL", ch, ext, rr.status_code, rr.text[:160], file=sys.stderr)

post("/api/tasks", {"title":"متابعة طلب لينا","problem":"العميلة بدها تعرف سعر الباقة الشهرية","priority":"high","status":"open","department":"Sales"})
post("/api/tasks", {"title":"Reserve small package for Omar","problem":"Customer asked to reserve one unit","priority":"normal","status":"in_progress"})
post("/api/tasks", {"title":"تجهيز الكتالوج بالإنكليزي","priority":"low","status":"open"})
post("/api/tasks", {"title":"Weekly report to the owner","priority":"normal","status":"resolved"})

post("/api/appointments", {"staff_user_id":USER_ID,"starts_at":"2026-09-05T09:00:00Z","ends_at":"2026-09-05T09:30:00Z","title":"استشارة — مايا رزق","status":"confirmed"})
post("/api/appointments", {"staff_user_id":USER_ID,"starts_at":"2026-09-05T11:00:00Z","ends_at":"2026-09-05T12:00:00Z","title":"Product demo — Omar Saad","status":"scheduled"})
post("/api/appointments", {"staff_user_id":USER_ID,"starts_at":"2026-09-06T14:00:00Z","ends_at":"2026-09-06T14:45:00Z","title":"متابعة — نور عون","status":"scheduled"})

post("/api/knowledge", {"title":"سياسة التوصيل","content_ar":"منوصّل لكل لبنان. رسوم بيروت ٢$ وخارج بيروت ٣$. التوصيل خلال ٢٤ ساعة.","content_en":"We deliver across Lebanon. Beirut $2, outside Beirut $3, within 24 hours.","department":"Sales","status":"active"})
post("/api/knowledge", {"title":"ساعات العمل","content_ar":"من الاثنين للجمعة، ٩ صباحاً حتى ٦ مساءً.","content_en":"Monday to Friday, 9am to 6pm.","status":"active"})

# ---- catalogue ----------------------------------------------------------
# The preview drew an empty Catalogue because nothing here had ever created a
# product, which read as "the module is unbuilt" rather than "this company sells
# nothing". Seeded through the real POSTs so the recorded rows carry the
# server's own shape -- category ids, currency, sale price and stock -- and not
# a hand-written guess at it.
CATEGORY_IDS = {}
for _name, _sort in (("باقات الاشتراك", 0), ("Accessories", 1), ("عروض", 2)):
    rr = post("/api/catalogue/categories", {"name": _name, "sort_order": _sort, "status": "active"})
    if rr.status_code < 400:
        CATEGORY_IDS[_name] = int(rr.json()["id"])

PRODUCTS = [
    {"name":"الباقة الشهرية","name_en":"Monthly package","sku":"PKG-M-01","brand":"Cedar Home",
     "category":"باقات الاشتراك","price":45,"currency":"USD","stock_quantity":120,
     "description":"باقة شهرية بتشمل التوصيل المجاني داخل بيروت.","status":"active"},
    {"name":"الباقة السنوية","name_en":"Annual package","sku":"PKG-Y-01","brand":"Cedar Home",
     "category":"باقات الاشتراك","price":480,"sale_price":420,"currency":"USD","stock_quantity":40,
     "description":"باقة سنوية مع خصم شهرين.","status":"active"},
    {"name":"Small package","sku":"PKG-S-01","brand":"Cedar Home","category":"باقات الاشتراك",
     "price":25,"currency":"USD","stock_quantity":3,
     "description":"Entry package — the one Omar asked us to reserve.","status":"active"},
    {"name":"Carry case","sku":"ACC-CASE-01","brand":"Falcon","category":"Accessories",
     "price":18.5,"currency":"USD","stock_quantity":0,"in_stock":False,
     "description":"Padded case. Out of stock until the next shipment.","status":"active"},
    {"name":"USB-C cable 2m","sku":"ACC-CBL-2M","brand":"Falcon","category":"Accessories",
     "price":7,"currency":"USD","stock_quantity":260,"status":"active"},
    {"name":"عرض الصيف","name_en":"Summer offer","sku":"PRM-SUM-26","category":"عروض",
     "price":60,"sale_price":45,"currency":"USD","stock_quantity":75,
     "description":"خصم ٢٥٪ لآخر الشهر.","status":"active"},
    {"name":"Gift voucher $50","sku":"PRM-GC-50","category":"عروض","price":50,"currency":"USD",
     "stock_quantity":30,"status":"draft"},
    {"name":"Legacy starter kit","sku":"PKG-OLD-00","brand":"Cedar Home","price":15,"currency":"USD",
     "stock_quantity":0,"in_stock":False,"status":"archived",
     "description":"Discontinued — kept so old invoices still resolve."},
]
for _product in PRODUCTS:
    _body = {k: v for k, v in _product.items() if k != "category"}
    _category = _product.get("category")
    if _category in CATEGORY_IDS:
        _body["category_id"] = CATEGORY_IDS[_category]
    post("/api/catalogue/products", _body)

post("/api/saved-replies", {"title":"ترحيب","body":"أهلاً وسهلاً فيك! كيف فينا نساعدك اليوم؟","department":""})
post("/api/saved-replies", {"title":"Delivery fees","body":"Delivery is $2 inside Beirut and $3 outside, arriving within 24 hours.","department":"Sales"})

# Two campaigns, both left as drafts. Sending one would call the real channels,
# and a preview must never message anybody -- so the recorded report is the
# not-sent-yet shape the detail screen draws for a draft.
BROADCASTS = [
    {"name":"عرض الصيف — قائمة أرقام","message_text":"عرض الصيف بلّش! خصم ٢٥٪ لآخر الشهر.","channel":"whatsapp",
     "numbers":["+961 71 555 010","+961 71 555 011","+961 3 555 012"]},
    {"name":"Telegram catalogue announcement","message_text":"Our English catalogue is out — reply CATALOGUE for a copy.","channel":"telegram"},
]
BROADCAST_IDS = []
for body in BROADCASTS:
    rr = post("/api/broadcasts", body)
    if rr.status_code < 400:
        BROADCAST_IDS.append(rr.json()["id"])

# ---- contacts -----------------------------------------------------------
# The register was empty in the preview because nothing here had ever created a
# customer: seeding a conversation stores messages, and it is `channels/inbound`
# that turns a sender into a contact, one `upsert_from_channel` per message. So
# make the same call it makes -- five people who wrote in, on the channel they
# wrote from -- and then one walk-in entered by hand through POST /api/customers,
# which is the "Add contact" button on the screen.
from backend.services.customer_service import customer_service

CONTACT_IDS = {}
for channel, ext, name, _msgs in PEOPLE:
    contact = customer_service.upsert_from_channel(
        company_id=COMPANY_ID, channel=channel, external_user_id=ext, display_name=name,
    )
    CONTACT_IDS[ext] = int(contact["id"])

walk_in = post("/api/customers", {"display_name": "Rita Ghanem", "phone": "+96170445566",
                                  "email": "rita.ghanem@example.com"})
if walk_in.status_code < 400:
    CONTACT_IDS["walk-in"] = int(walk_in.json()["id"])

def put(path, body):
    r = client.put(path, json=body, headers=AUTH)
    if r.status_code >= 400:
        print("SEED FAIL", path, r.status_code, r.text[:200], file=sys.stderr)
    return r

# Stage, tags, owner and the client file's own fields, through the same PUT the
# screen sends when an employee changes a dropdown or types a tag.
put(f"/api/customers/{CONTACT_IDS['cust-lina']}", {
    "display_name": "لينا خوري", "phone": "+96171223344", "language": "ar",
    "country": "Lebanon", "lifecycle_stage": "vip", "tags": ["المنصورية", "توصيل"],
    "assigned_user_id": USER_ID,
    "custom_fields": {"العنوان": "المنصورية - شارع المدارس", "الباقة": "شهرية"},
    "documents": [{"label": "بطاقة الهوية", "url": "https://files.example.com/lina-id.pdf"}],
    "notes": "بتفضل التواصل عالماسنجر بعد الظهر.",
})
put(f"/api/customers/{CONTACT_IDS['cust-omar']}", {
    "display_name": "Omar Saad", "phone": "+96176889900", "email": "omar.saad@example.com",
    "language": "en", "country": "Lebanon", "lifecycle_stage": "customer",
    "tags": ["reserved", "small package"], "assigned_user_id": USER_ID,
})
put(f"/api/customers/{CONTACT_IDS['cust-nour']}", {
    "display_name": "نور عون", "lifecycle_stage": "active", "tags": ["عرض الشهر"],
})
put(f"/api/customers/{CONTACT_IDS['cust-karim']}", {
    "display_name": "Karim Fares", "email": "karim.fares@example.com",
    "lifecycle_stage": "lead", "tags": ["catalogue"],
})
put(f"/api/customers/{CONTACT_IDS['cust-maya']}", {
    "display_name": "مايا رزق", "phone": "+96103557788", "lifecycle_stage": "customer",
    "tags": ["مواعيد"], "assigned_user_id": USER_ID,
})
if "walk-in" in CONTACT_IDS:
    put(f"/api/customers/{CONTACT_IDS['walk-in']}", {"lifecycle_stage": "lead", "tags": ["walk-in"]})

post("/api/customer-segments", {"name": "VIP clients", "filters": {"lifecycle_stage": "vip"}})
post("/api/customer-segments", {"name": "Waiting on delivery", "filters": {"tag": "توصيل"}})

# ---- capture every GET the frontend makes -------------------------------
GETS = [
    "/api/auth/me", "/api/platform-ui/config", "/api/dashboard/summary",
    "/conversations/", "/conversations/options",
    "/api/tasks", "/api/tasks/options",
    "/api/appointments", "/api/appointments/options",
    "/api/notifications", "/api/notifications/summary",
    "/api/customers", "/api/customers/options", "/api/customer-segments",
    "/api/knowledge", "/api/knowledge/options", "/api/knowledge/categories",
    "/api/saved-replies", "/api/catalogue/products",
    # The catalogue screen builds its category filter and its editor's category
    # dropdown from /options. Without it the filter was permanently empty and
    # every product read as uncategorised.
    "/api/catalogue/options", "/api/catalogue/categories",
    # Reporting: the screen draws message volume, channel mix and reply health
    # from this one summary, so without it the preview shows its empty state and
    # reads as though the feature were unbuilt.
    "/api/analytics/summary",
    "/api/admin/access/overview", "/api/admin/access/roles",
    "/api/admin/access/users", "/api/admin/access/branches", "/api/channels", "/api/dashboard/channels",
    "/api/broadcasts",
]

# The Broadcast detail screen reads one campaign's report, and the send dialog
# recounts its recipients. Both are per-id, so they are added once the seeded
# ids are known rather than written out above.
for broadcast_id in BROADCAST_IDS:
    GETS.append(f"/api/broadcasts/{broadcast_id}/report")
    GETS.append(f"/api/broadcasts/{broadcast_id}/recipient-count")
# The five report tables the Analytics screen exports. They are keyed by path
# *and* query, because unlike every other capture here the answer differs per
# `report=` -- and they are files, not JSON, so the text and the headers that
# name the download are recorded alongside the status.
GETS += [f"/api/analytics/export?report={name}"
         for name in ("employees", "departments", "channels", "volume", "response")]

captured = {}
for path in GETS:
    r = client.get(path, headers=AUTH)
    is_json = r.headers.get("content-type", "").startswith("application/json")
    entry = {"status": r.status_code, "body": r.json() if is_json else None}
    if not is_json:
        entry["text"] = r.text
        entry["content_type"] = r.headers.get("content-type")
        entry["content_disposition"] = r.headers.get("content-disposition")
    captured[path] = entry
    print(f"{r.status_code}  {path}")

for channel, ext, *_ in PEOPLE:
    p = f"/conversations/{channel}/{ext}"
    r = client.get(p, params={"mark_read": "false"}, headers=AUTH)
    captured[p] = {"status": r.status_code, "body": r.json() if r.status_code == 200 else None}
    print(f"{r.status_code}  {p}")

# The client file at /customers/:id reads two paths per contact. Capturing both
# for every seeded contact is what lets the preview open a real profile instead
# of the empty-but-valid shape the demo falls back to.
for _ext, _cid in sorted(CONTACT_IDS.items(), key=lambda item: item[1]):
    for p in (f"/api/customers/{_cid}", f"/api/customers/{_cid}/timeline"):
        r = client.get(p, headers=AUTH)
        captured[p] = {"status": r.status_code, "body": r.json() if r.status_code == 200 else None}
        print(f"{r.status_code}  {p}")

captured["/api/auth/login"] = {"status": 200, "body": LOGIN_BODY}
json.dump(captured, open(OUT, "w"), ensure_ascii=False, indent=1)
print("\ncaptured", len(captured), "endpoints ->", OUT)
