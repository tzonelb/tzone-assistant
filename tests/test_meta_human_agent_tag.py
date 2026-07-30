"""
Real tests for the Messenger HUMAN_AGENT messaging tag.

Meta rejects messages sent outside the 24-hour window unless tagged.
When a real employee (not the AI) sends a manual reply, we now tag it
HUMAN_AGENT, which Meta allows for up to 7 days from the customer's
last message — this is exactly the scenario the tag exists for.

Run with: python3 -m pytest tests/test_meta_human_agent_tag.py -v
"""
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_human_agent_reply_includes_message_tag():
    from channels.meta.sender import send_meta_text

    fake_response = MagicMock()
    fake_response.ok = True
    fake_response.status_code = 200
    fake_response.content = b'{"message_id": "m1"}'
    fake_response.json.return_value = {"message_id": "m1"}

    with patch("channels.meta.sender.config.META_PAGE_ACCESS_TOKEN", "fake-token"), \
         patch("channels.meta.sender.requests.post", return_value=fake_response) as mock_post:
        send_meta_text(
            recipient_id="1234567890", text="Hi, following up on your request",
            channel="messenger", is_human_agent=True,
        )

    sent_payload = mock_post.call_args.kwargs["json"]
    assert sent_payload["messaging_type"] == "MESSAGE_TAG"
    assert sent_payload["tag"] == "HUMAN_AGENT"


def test_ai_reply_does_not_include_message_tag():
    """AI auto-replies must stay within the normal 24h window — using
    HUMAN_AGENT for automated messages risks Meta revoking the tag."""
    from channels.meta.sender import send_meta_text

    fake_response = MagicMock()
    fake_response.ok = True
    fake_response.status_code = 200
    fake_response.content = b'{"message_id": "m2"}'
    fake_response.json.return_value = {"message_id": "m2"}

    with patch("channels.meta.sender.config.META_PAGE_ACCESS_TOKEN", "fake-token"), \
         patch("channels.meta.sender.requests.post", return_value=fake_response) as mock_post:
        send_meta_text(
            recipient_id="1234567890", text="Hello! How can I help?", channel="messenger",
        )  # is_human_agent defaults to False

    sent_payload = mock_post.call_args.kwargs["json"]
    assert "messaging_type" not in sent_payload
    assert "tag" not in sent_payload


def test_human_agent_tag_not_applied_to_instagram():
    """HUMAN_AGENT is a Messenger-only tag — Instagram has different
    windowing rules and this tag isn't valid there."""
    from channels.meta.sender import send_meta_text

    fake_response = MagicMock()
    fake_response.ok = True
    fake_response.status_code = 200
    fake_response.content = b'{"message_id": "m3"}'
    fake_response.json.return_value = {"message_id": "m3"}

    with patch("channels.meta.sender.config.META_PAGE_ACCESS_TOKEN", "fake-token"), \
         patch("channels.meta.sender.requests.post", return_value=fake_response) as mock_post:
        send_meta_text(
            recipient_id="1234567890", text="Following up", channel="instagram", is_human_agent=True,
        )

    sent_payload = mock_post.call_args.kwargs["json"]
    assert "messaging_type" not in sent_payload


def test_human_agent_reply_with_buttons_includes_tag():
    from channels.meta.sender import send_meta_buttons

    fake_response = MagicMock()
    fake_response.ok = True
    fake_response.status_code = 200
    fake_response.content = b'{"message_id": "m4"}'
    fake_response.json.return_value = {"message_id": "m4"}

    with patch("channels.meta.sender.config.META_PAGE_ACCESS_TOKEN", "fake-token"), \
         patch("channels.meta.sender.requests.post", return_value=fake_response) as mock_post:
        send_meta_buttons(
            recipient_id="1234567890", text="Pick one", buttons=["A", "B"],
            channel="messenger", is_human_agent=True,
        )

    sent_payload = mock_post.call_args.kwargs["json"]
    assert sent_payload["messaging_type"] == "MESSAGE_TAG"
    assert sent_payload["tag"] == "HUMAN_AGENT"


def test_unapproved_tag_falls_back_and_still_sends():
    """When Meta rejects the HUMAN_AGENT tag itself (app not approved
    yet — error code 100, 'without prior approval'), retry once
    without the tag instead of hard-failing the whole send."""
    from channels.meta.sender import send_meta_text

    rejected_response = MagicMock()
    rejected_response.ok = False
    rejected_response.status_code = 400
    rejected_response.content = b'{"error": {"code": 100, "message": "Cannot tag messages with \\"HUMAN_AGENT\\" without prior approval."}}'
    rejected_response.json.return_value = {
        "error": {"code": 100, "message": 'Cannot tag messages with "HUMAN_AGENT" without prior approval.'}
    }

    success_response = MagicMock()
    success_response.ok = True
    success_response.status_code = 200
    success_response.content = b'{"message_id": "m5"}'
    success_response.json.return_value = {"message_id": "m5"}

    with patch("channels.meta.sender.config.META_PAGE_ACCESS_TOKEN", "fake-token"), \
         patch("channels.meta.sender.requests.post", side_effect=[rejected_response, success_response]) as mock_post:
        result = send_meta_text(
            recipient_id="1234567890", text="hi", channel="messenger", is_human_agent=True,
        )

    assert result["ok"] is True
    assert mock_post.call_count == 2
    first_call_payload = mock_post.call_args_list[0].kwargs["json"]
    second_call_payload = mock_post.call_args_list[1].kwargs["json"]
    assert first_call_payload.get("tag") == "HUMAN_AGENT"
    assert "tag" not in second_call_payload
    assert "messaging_type" not in second_call_payload


def test_unapproved_tag_falls_back_even_with_200_status():
    """Regression guard for the real bug found in production: Meta can
    return this specific error with an HTTP 200 status, embedding the
    error only in the response body. The fallback must trigger based
    on the error content itself, not response.ok — this test would
    have caught the original bug (which only checked response.ok)."""
    from channels.meta.sender import send_meta_text

    rejected_response = MagicMock()
    rejected_response.ok = True  # <- the quirk: 200 status despite an error body
    rejected_response.status_code = 200
    rejected_response.content = b'{"error": {"code": 100, "message": "Cannot tag messages with \\"HUMAN_AGENT\\" without prior approval."}}'
    rejected_response.json.return_value = {
        "error": {"code": 100, "message": 'Cannot tag messages with "HUMAN_AGENT" without prior approval.'}
    }

    success_response = MagicMock()
    success_response.ok = True
    success_response.status_code = 200
    success_response.content = b'{"message_id": "m6"}'
    success_response.json.return_value = {"message_id": "m6"}

    with patch("channels.meta.sender.config.META_PAGE_ACCESS_TOKEN", "fake-token"), \
         patch("channels.meta.sender.requests.post", side_effect=[rejected_response, success_response]) as mock_post:
        result = send_meta_text(
            recipient_id="1234567890", text="hi", channel="messenger", is_human_agent=True,
        )

    assert result["ok"] is True
    assert mock_post.call_count == 2
    assert result["response"]["message_id"] == "m6"


def test_genuine_24h_window_error_does_not_trigger_fallback_retry():
    """A real 'outside the allowed window' error (code #10, not a tag
    approval issue) should NOT trigger a retry — there's nothing a
    retry could fix."""
    from channels.meta.sender import send_meta_text

    rejected_response = MagicMock()
    rejected_response.ok = False
    rejected_response.status_code = 400
    rejected_response.content = b'{"error": {"code": 10, "message": "This message is being sent outside the allowed window."}}'
    rejected_response.json.return_value = {
        "error": {"code": 10, "message": "This message is being sent outside the allowed window."}
    }

    with patch("channels.meta.sender.config.META_PAGE_ACCESS_TOKEN", "fake-token"), \
         patch("channels.meta.sender.requests.post", return_value=rejected_response) as mock_post:
        result = send_meta_text(recipient_id="1234567890", text="hi", channel="messenger")

    assert result["ok"] is False
    assert mock_post.call_count == 1
