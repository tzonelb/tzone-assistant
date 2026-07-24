"""
Real test for the Instagram/Messenger channel mislabeling bug found while
verifying "Patch 9.1" claims: channels/meta/parser.py had a working
detect_meta_channel() helper that correctly reads payload["object"], but
both parse_from_messaging() and parse_from_changes() hardcoded
"channel": "messenger" regardless of the actual source, so every
Instagram message would have been silently stored and displayed as a
Messenger conversation.

Run with: python3 -m pytest tests/test_meta_channel_detection.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from channels.meta.parser import parse_meta_text_message  # noqa: E402


def _messaging_payload(object_type: str) -> dict:
    return {
        "object": object_type,
        "entry": [
            {
                "messaging": [
                    {
                        "sender": {"id": "123"},
                        "recipient": {"id": "456"},
                        "message": {"text": "hello"},
                    }
                ]
            }
        ],
    }


def _changes_payload(object_type: str) -> dict:
    return {
        "object": object_type,
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "sender": {"id": "123"},
                            "recipient": {"id": "456"},
                            "message": {"text": "hello"},
                        },
                    }
                ]
            }
        ],
    }


def test_messenger_messaging_payload_labeled_messenger():
    result = parse_meta_text_message(_messaging_payload("page"))
    assert result["channel"] == "messenger"


def test_instagram_messaging_payload_labeled_instagram():
    result = parse_meta_text_message(_messaging_payload("instagram"))
    assert result["channel"] == "instagram"


def test_messenger_changes_payload_labeled_messenger():
    result = parse_meta_text_message(_changes_payload("page"))
    assert result["channel"] == "messenger"


def test_instagram_changes_payload_labeled_instagram():
    result = parse_meta_text_message(_changes_payload("instagram"))
    assert result["channel"] == "instagram"
