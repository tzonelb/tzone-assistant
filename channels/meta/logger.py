import json
from datetime import datetime
from pathlib import Path

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

META_LOG_FILE = LOG_DIR / "meta_messages.log"


def log_meta_event(event_type: str, data: dict):
    record = {
        "time": datetime.utcnow().isoformat(),
        "event_type": event_type,
        "data": data,
    }

    with open(META_LOG_FILE, "a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")