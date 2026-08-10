import json
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASE_DIR = PROJECT_ROOT / "data" / "conversations"
BASE_DIR.mkdir(parents=True, exist_ok=True)


def _safe_name(value: str) -> str:
    return str(value).replace("/", "_").replace("\\", "_").replace(":", "_")


def _company_dir(company_id: int) -> Path:
    # SECURITY: company_id is the tenant boundary for this store. It used to
    # be absent from the storage key entirely (BASE_DIR/{channel}/{user}.jsonl)
    # — any authenticated user of ANY company could read another company's
    # full conversation history (customer PII, everything a customer ever
    # typed) just by knowing/guessing a channel+external_user_id (e.g. a
    # phone number), and the inbox list leaked it with zero guessing needed
    # (it globbed every file on disk with no ownership check). Fixed by
    # scoping the storage key to company_id; every call site below now
    # requires it.
    return BASE_DIR / _safe_name(str(company_id))


def save_conversation_message(
    company_id: int,
    channel: str,
    user_id: str,
    direction: str,
    text: str,
    metadata: dict | None = None,
):
    channel = _safe_name(channel)
    user_id = _safe_name(user_id)

    folder = _company_dir(company_id) / channel
    folder.mkdir(parents=True, exist_ok=True)

    file_path = folder / f"{user_id}.jsonl"

    record = {
        "time": datetime.utcnow().isoformat(),
        "channel": channel,
        "user_id": user_id,
        "direction": direction,
        "text": text,
        "metadata": metadata or {},
    }

    with open(file_path, "a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record


def get_conversation(company_id: int, channel: str, user_id: str, limit: int = 50):
    channel = _safe_name(channel)
    user_id = _safe_name(user_id)

    file_path = _company_dir(company_id) / channel / f"{user_id}.jsonl"

    if not file_path.exists():
        return []

    lines = file_path.read_text(encoding="utf-8").splitlines()
    rows = []

    for line in lines[-limit:]:
        try:
            rows.append(json.loads(line))
        except Exception:
            rows.append({"raw": line})

    return rows