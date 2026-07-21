from fastapi import APIRouter
from pydantic import BaseModel
from typing import Any

from channels.meta.processor import process_meta_payload

router = APIRouter(tags=["Meta Tester"])


class MetaPayloadTest(BaseModel):
    payload: dict[str, Any]


@router.post("/test/meta-payload")
def test_meta_payload(body: MetaPayloadTest):
    return process_meta_payload(body.payload)


@router.post("/test/meta-message")
def test_meta_message():
    payload = {
        "object": "page",
        "entry": [
            {
                "messaging": [
                    {
                        "sender": {"id": "123456789"},
                        "recipient": {"id": "987654321"},
                        "message": {
                            "mid": "test123",
                            "text": "Hello from Messenger test"
                        }
                    }
                ]
            }
        ]
    }

    return process_meta_payload(payload)