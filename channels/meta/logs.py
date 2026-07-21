from fastapi import APIRouter
from pathlib import Path
import json

router = APIRouter(tags=["Meta Logs"])

LOG_FILE = Path("logs/meta_messages.log")


@router.get("/logs/meta")
def get_meta_logs(limit: int = 50):
    if not LOG_FILE.exists():
        return {
            "status": "ok",
            "logs": [],
        }

    lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
    latest = lines[-limit:]

    logs = []

    for line in latest:
        try:
            logs.append(json.loads(line))
        except Exception:
            logs.append({"raw": line})

    return {
        "status": "ok",
        "count": len(logs),
        "logs": logs,
    }


@router.delete("/logs/meta")
def clear_meta_logs():
    LOG_FILE.parent.mkdir(exist_ok=True)
    LOG_FILE.write_text("", encoding="utf-8")

    return {
        "status": "ok",
        "message": "Meta logs cleared",
    }