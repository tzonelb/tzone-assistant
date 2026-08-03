"""
Tests for AIRouter.apply_guardrails()'s premature-transfer gate: a
customer's FIRST unmatched message must not be flagged needs_human (the
model's own clarifying question should get a chance first) — only a second
consecutive unmatched turn on the same stuck topic escalates for real.
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ai_router import ai_router


def _base_result(**overrides):
    result = {
        "department": "unknown",
        "intent": "unknown",
        "topic": "unknown",
        "language": "ar",
        "confidence": 0.4,
        "reply": "ما فهمت طلبك، ممكن توضح أكتر؟",
        "buttons": [],
        "needs_human": False,
        "missing_information": [],
        "used_knowledge_ids": [],
        "notes": "",
    }
    result.update(overrides)
    return result


def test_first_unmatched_turn_does_not_escalate():
    result = ai_router.apply_guardrails(
        result=_base_result(),
        message="مرحبا",
        knowledge=[],
        connector_results=[],
        previously_needed_human=False,
    )
    assert result["needs_human"] is False


def test_second_consecutive_unmatched_turn_escalates():
    result = ai_router.apply_guardrails(
        result=_base_result(),
        message="لسا ما فهمت شو قصدك",
        knowledge=[],
        connector_results=[],
        previously_needed_human=True,
    )
    assert result["needs_human"] is True
    assert "verified business information" in result["missing_information"]


def test_price_question_still_escalates_immediately_even_on_first_turn():
    """Unlike the generic no-knowledge-match case, a price/stock/balance
    question the AI genuinely cannot verify (no connector integrated) isn't
    something a clarifying question can fix — this guardrail is untouched
    by the premature-transfer gate."""
    result = ai_router.apply_guardrails(
        result=_base_result(reply="the plan costs $10"),
        message="how much does it cost?",
        knowledge=[],
        connector_results=[],
        previously_needed_human=False,
    )
    assert result["needs_human"] is True


def test_knowledge_present_never_escalates_via_this_guardrail():
    result = ai_router.apply_guardrails(
        result=_base_result(reply="You can find our office on Main Street."),
        message="where is your office?",
        knowledge=[{"id": "1", "title": "Office location"}],
        connector_results=[],
        previously_needed_human=False,
    )
    assert result["needs_human"] is False


def _mock_openai_text_response(text):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"output_text": text}
    return response


def test_summarize_for_handoff_returns_real_ai_text():
    with patch("core.ai_router.config.AI_ENABLED", True), \
         patch("core.ai_router.config.OPENAI_API_KEY", "test-key"), \
         patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = _mock_openai_text_response("Customer wants a refund for a broken router; AI has no refund policy knowledge.")
        mock_client_cls.return_value = mock_client

        summary = ai_router.summarize_for_handoff(
            conversation_history=[{"role": "customer", "text": "my router is broken, I want a refund"}],
            last_user_message="my router is broken, I want a refund",
            topic="refund",
            missing_information=["refund policy"],
            language="en",
        )
    assert summary == "Customer wants a refund for a broken router; AI has no refund policy knowledge."


def test_summarize_for_handoff_degrades_gracefully_without_api_key():
    with patch("core.ai_router.config.OPENAI_API_KEY", ""):
        summary = ai_router.summarize_for_handoff(
            conversation_history=[],
            last_user_message="hi",
            topic="unknown",
            missing_information=[],
            language="en",
        )
    assert summary is None


def test_summarize_for_handoff_degrades_gracefully_on_api_error():
    with patch("core.ai_router.config.AI_ENABLED", True), \
         patch("core.ai_router.config.OPENAI_API_KEY", "test-key"), \
         patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        error_response = MagicMock()
        error_response.status_code = 500
        error_response.text = "server error"
        mock_client.post.return_value = error_response
        mock_client_cls.return_value = mock_client

        summary = ai_router.summarize_for_handoff(
            conversation_history=[],
            last_user_message="hi",
            topic="unknown",
            missing_information=[],
            language="en",
        )
    assert summary is None
