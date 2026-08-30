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

COMPANY_NAME = "T-ZONE Demo"
with manager.control() as conn:
    now = utc_now_iso()
    conn.execute("INSERT INTO workspaces (name, slug, status, created_at, updated_at)"
                 " VALUES ('T-ZONE', 'tzone', 'active', ?, ?)", (now, now))
    cur = conn.execute("INSERT INTO companies (workspace_id, name, slug, status, created_at, updated_at)"
                       " VALUES (1, ?, 'tzone-demo', 'active', ?, ?)", (COMPANY_NAME, now, now))
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
EMAIL, PASSWORD = "demo@tz-lb.com", "DemoOwner123!"
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
                                activity)

app = FastAPI()
for module in (auth, platform_ui, dashboard, conversations, manual_messages, conversation_tags,
               saved_replies, customers, knowledge, catalogue, comments, scheduler, appointments,
               team_chat, notifications, roles, analytics, ai_teaching, channels,
               company_settings, activity):
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

PEOPLE = [
    ("messenger", "cust-lina",  "Lina Khoury",   [("in","مرحبا، بدي اعرف اذا في توصيل عالمنصورية؟"),("out","أهلاً لينا! نعم منوصّل عالمنصورية، الرسوم ٣ دولار والتوصيل خلال ٢٤ ساعة."),("in","تمام، وكم سعر الباقة الشهرية؟")]),
    ("whatsapp",  "cust-omar",  "Omar Saad",     [("in","Hi, is the small package still available?"),("out","Hello Omar — yes, it is. Would you like me to reserve one for you?"),("in","Yes please, reserve it under my name.")]),
    ("instagram", "cust-nour",  "Nour Aoun",     [("in","شفت البوست تبع العرض، لسا شغال؟"),("out","أهلاً نور! العرض شغال لآخر الشهر.")]),
    ("telegram",  "cust-karim", "Karim Fares",   [("in","Do you have an English catalogue?"),("out","We do — sending it over now.")]),
    ("messenger", "cust-maya",  "Maya Rizk",     [("in","بدي غيّر موعدي من الخميس للسبت"),("out","أكيد مايا، عدّلتلك الموعد للسبت الساعة ١١ صباحاً.")]),
]

for channel, ext, name, msgs in PEOPLE:
    conversation_control_service.get_or_create(company_id=COMPANY_ID, channel=channel, external_user_id=ext)
    for direction, text in msgs:
        message_service.save_message(
            company_id=COMPANY_ID, channel=channel, external_user_id=ext,
            direction=direction, text=text,
            sender_type="customer" if direction == "in" else "bot",
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
    ("messenger", "T-ZONE Page",      {"page_id": "104857600011"}),
    ("whatsapp",  "T-ZONE WhatsApp",  {"phone_number_id": "159357456123"}),
    ("instagram", "T-ZONE Instagram", {"instagram_business_id": "178899220033"}),
    ("telegram",  "T-ZONE Telegram",  {"access_token": "7654321098:AAHdemoBotTokenForThePreviewSite01234"}),
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

post("/api/tasks", {"title":"متابعة طلب لينا","problem":"العميلة بدها تعرف سعر الباقة الشهرية","priority":"high","status":"open","department":"Sales"})
post("/api/tasks", {"title":"Reserve small package for Omar","problem":"Customer asked to reserve one unit","priority":"normal","status":"in_progress"})
post("/api/tasks", {"title":"تجهيز الكتالوج بالإنكليزي","priority":"low","status":"open"})
post("/api/tasks", {"title":"Weekly report to the owner","priority":"normal","status":"resolved"})

post("/api/appointments", {"staff_user_id":USER_ID,"starts_at":"2026-09-05T09:00:00Z","ends_at":"2026-09-05T09:30:00Z","title":"استشارة — مايا رزق","status":"confirmed"})
post("/api/appointments", {"staff_user_id":USER_ID,"starts_at":"2026-09-05T11:00:00Z","ends_at":"2026-09-05T12:00:00Z","title":"Product demo — Omar Saad","status":"scheduled"})
post("/api/appointments", {"staff_user_id":USER_ID,"starts_at":"2026-09-06T14:00:00Z","ends_at":"2026-09-06T14:45:00Z","title":"متابعة — نور عون","status":"scheduled"})

post("/api/knowledge", {"title":"سياسة التوصيل","content_ar":"منوصّل لكل لبنان. رسوم بيروت ٢$ وخارج بيروت ٣$. التوصيل خلال ٢٤ ساعة.","content_en":"We deliver across Lebanon. Beirut $2, outside Beirut $3, within 24 hours.","department":"Sales","status":"active"})
post("/api/knowledge", {"title":"ساعات العمل","content_ar":"من الاثنين للجمعة، ٩ صباحاً حتى ٦ مساءً.","content_en":"Monday to Friday, 9am to 6pm.","status":"active"})

post("/api/saved-replies", {"title":"ترحيب","body":"أهلاً وسهلاً فيك! كيف فينا نساعدك اليوم؟","department":""})
post("/api/saved-replies", {"title":"Delivery fees","body":"Delivery is $2 inside Beirut and $3 outside, arriving within 24 hours.","department":"Sales"})

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
    "/api/admin/access/overview", "/api/admin/access/roles",
    "/api/admin/access/users", "/api/admin/access/branches", "/api/channels", "/api/dashboard/channels",
]
captured = {}
for path in GETS:
    r = client.get(path, headers=AUTH)
    captured[path] = {"status": r.status_code,
                      "body": r.json() if r.headers.get("content-type","").startswith("application/json") else None}
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
