from fastapi import FastAPI

from backend.api.routes import (
    health,
    tickets,
    knowledge,
    test_whatsapp,
    conversations,
    broadcasts,
)
from channels.whatsapp import webhook as whatsapp_webhook
from channels.meta import webhook as meta_webhook
from channels.meta import debug as meta_debug
from channels.meta import tester as meta_tester
from channels.meta import logs as meta_logs

app = FastAPI(
    title="T-ZONE Platform API",
    version="2.2.5"
)

app.include_router(health.router)
app.include_router(tickets.router)
app.include_router(knowledge.router)
app.include_router(test_whatsapp.router)
app.include_router(conversations.router)
app.include_router(broadcasts.router)

app.include_router(whatsapp_webhook.router)
app.include_router(meta_webhook.router)
app.include_router(meta_debug.router)
app.include_router(meta_tester.router)
app.include_router(meta_logs.router)


@app.get("/")
def home():
    return {
        "app": "T-ZONE Platform API",
        "status": "running",
        "version": "2.2.5",
    }