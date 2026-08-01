import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_tts_service_calls_openai_and_returns_audio_bytes():
    from core.tts_service import tts_service

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.content = b"fake-mp3-bytes"

    mock_client = MagicMock()
    mock_client.__enter__.return_value.post.return_value = fake_response

    with patch("core.tts_service.config.OPENAI_API_KEY", "fake-key"), \
         patch("core.tts_service.httpx.Client", return_value=mock_client):
        audio = tts_service.generate_speech("Hello there!")

    assert audio == b"fake-mp3-bytes"
    sent_kwargs = mock_client.__enter__.return_value.post.call_args.kwargs
    assert sent_kwargs["json"]["input"] == "Hello there!"


def test_tts_service_raises_on_missing_key():
    from core.tts_service import tts_service

    with patch("core.tts_service.config.OPENAI_API_KEY", ""):
        try:
            tts_service.generate_speech("hi")
            assert False, "expected ValueError"
        except ValueError:
            pass


def test_tts_service_raises_on_api_error():
    from core.tts_service import tts_service

    fake_response = MagicMock()
    fake_response.status_code = 401
    fake_response.text = "unauthorized"

    mock_client = MagicMock()
    mock_client.__enter__.return_value.post.return_value = fake_response

    with patch("core.tts_service.config.OPENAI_API_KEY", "fake-key"), \
         patch("core.tts_service.httpx.Client", return_value=mock_client):
        try:
            tts_service.generate_speech("hi")
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass


def _run_finish_pending(*, voice_reply_enabled, plan_voice_ai_enabled):
    import channels.meta.smart_reply as smart_reply_module

    fake_response = MagicMock()
    fake_response.text = "Hello, how can we help?"
    fake_response.buttons = None

    settings = {"enabled": True, "voice_reply_enabled": voice_reply_enabled}

    with patch("channels.meta.smart_reply.message_gateway") as mock_gateway:
        mock_gateway.handle_text.return_value = fake_response
        with patch("channels.meta.smart_reply.company_settings_service") as mock_settings:
            mock_settings.get_section.return_value = {"values": settings}
            with patch("channels.meta.smart_reply.conversation_control_service") as mock_ccs:
                mock_ccs.record_ai_reply.return_value = {}
                with patch("channels.meta.smart_reply.platform_admin_service") as mock_platform_admin:
                    mock_platform_admin.get_active_subscription_limits.return_value = (
                        {"voice_ai_enabled": 1} if plan_voice_ai_enabled else {"voice_ai_enabled": 0}
                    )
                    with patch("channels.meta.smart_reply.tts_service") as mock_tts:
                        mock_tts.generate_speech.return_value = b"fake-audio"
                        with patch("channels.meta.smart_reply.media_upload_service") as mock_upload:
                            mock_upload.save_upload.return_value = {"url": "http://example.test/audio.mp3", "media_type": "audio"}
                            with patch("channels.meta.smart_reply.save_conversation_message"):
                                with patch("channels.meta.smart_reply._record_sent_status", return_value=None):
                                    with patch("channels.meta.smart_reply.send_meta_buttons", return_value={"ok": True, "response": {}}) as mock_text_send:
                                        with patch("channels.meta.smart_reply.send_meta_media", return_value={"ok": True, "response": {}}) as mock_media_send:
                                            with patch("channels.meta.smart_reply.diagnostics_service"):
                                                smart_reply_module.schedule_smart_reply(
                                                    channel="messenger", user_id="cust-1", company_id=42,
                                                    message="hi", delay_seconds=0,
                                                )
                                                key = smart_reply_module._key(42, "messenger", "cust-1")
                                                pending = smart_reply_module._PENDING[key]
                                                smart_reply_module._finish_pending(
                                                    company_id=42, channel="messenger", user_id="cust-1",
                                                    generation=pending.generation,
                                                )
    return mock_text_send, mock_media_send, mock_tts


def test_voice_reply_sent_when_enabled_and_plan_allows():
    text_send, media_send, tts = _run_finish_pending(voice_reply_enabled=True, plan_voice_ai_enabled=True)
    tts.generate_speech.assert_called_once()
    media_send.assert_called_once()
    text_send.assert_not_called()


def test_voice_reply_falls_back_to_text_when_plan_does_not_allow():
    text_send, media_send, tts = _run_finish_pending(voice_reply_enabled=True, plan_voice_ai_enabled=False)
    tts.generate_speech.assert_not_called()
    media_send.assert_not_called()
    text_send.assert_called_once()


def test_voice_reply_falls_back_to_text_when_company_has_not_opted_in():
    text_send, media_send, tts = _run_finish_pending(voice_reply_enabled=False, plan_voice_ai_enabled=True)
    tts.generate_speech.assert_not_called()
    media_send.assert_not_called()
    text_send.assert_called_once()
