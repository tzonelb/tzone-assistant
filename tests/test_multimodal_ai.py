import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_stt_service_transcribes_audio():
    from core.stt_service import stt_service

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"text": "hello there"}

    mock_client = MagicMock()
    mock_client.__enter__.return_value.post.return_value = fake_response

    with patch("core.stt_service.config.OPENAI_API_KEY", "fake-key"), \
         patch("core.stt_service.httpx.Client", return_value=mock_client):
        text = stt_service.transcribe(b"fake-audio-bytes")

    assert text == "hello there"


def test_stt_service_raises_on_api_error():
    from core.stt_service import stt_service

    fake_response = MagicMock()
    fake_response.status_code = 400
    fake_response.text = "bad audio"

    mock_client = MagicMock()
    mock_client.__enter__.return_value.post.return_value = fake_response

    with patch("core.stt_service.config.OPENAI_API_KEY", "fake-key"), \
         patch("core.stt_service.httpx.Client", return_value=mock_client):
        try:
            stt_service.transcribe(b"fake-audio-bytes")
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass


def test_stt_service_rejects_empty_audio():
    from core.stt_service import stt_service

    try:
        stt_service.transcribe(b"")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_vision_service_describes_image():
    from core.vision_service import vision_service

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"output_text": "A red sedan parked outside a shop"}

    mock_client = MagicMock()
    mock_client.__enter__.return_value.post.return_value = fake_response

    with patch("core.vision_service.config.OPENAI_API_KEY", "fake-key"), \
         patch("core.vision_service.httpx.Client", return_value=mock_client):
        description = vision_service.describe_image(b"fake-image-bytes", mime_type="image/png")

    assert description == "A red sedan parked outside a shop"
    sent_kwargs = mock_client.__enter__.return_value.post.call_args.kwargs
    image_part = sent_kwargs["json"]["input"][0]["content"][1]
    assert image_part["type"] == "input_image"
    assert image_part["image_url"].startswith("data:image/png;base64,")


def test_vision_service_raises_on_api_error():
    from core.vision_service import vision_service

    fake_response = MagicMock()
    fake_response.status_code = 500
    fake_response.text = "server error"

    mock_client = MagicMock()
    mock_client.__enter__.return_value.post.return_value = fake_response

    with patch("core.vision_service.config.OPENAI_API_KEY", "fake-key"), \
         patch("core.vision_service.httpx.Client", return_value=mock_client):
        try:
            vision_service.describe_image(b"fake-image-bytes")
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass


def test_download_whatsapp_media_two_step_fetch():
    from channels.whatsapp.media import download_whatsapp_media

    lookup_response = MagicMock()
    lookup_response.status_code = 200
    lookup_response.json.return_value = {"url": "https://lookaside.example/file", "mime_type": "audio/ogg"}

    download_response = MagicMock()
    download_response.status_code = 200
    download_response.content = b"raw-bytes"

    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.side_effect = [lookup_response, download_response]

    with patch("channels.whatsapp.media._resolve_whatsapp_credentials", return_value=("phone-id", "tok")), \
         patch("channels.whatsapp.media.httpx.Client", return_value=mock_client):
        result = download_whatsapp_media("media-1", company_id=1)

    assert result == (b"raw-bytes", "audio/ogg")


def test_download_whatsapp_media_returns_none_without_token():
    from channels.whatsapp.media import download_whatsapp_media

    with patch("channels.whatsapp.media._resolve_whatsapp_credentials", return_value=(None, None)):
        assert download_whatsapp_media("media-1", company_id=1) is None


def _fake_telegram_update(*, voice=None, photo=None, caption=None):
    update = MagicMock()
    update.message.voice = voice
    update.message.photo = photo
    update.message.caption = caption
    update.effective_user.id = 555
    update.effective_user.full_name = "Test Customer"
    update.effective_user.username = "testcustomer"
    return update


def test_telegram_voice_handler_transcribes_and_forwards_text():
    from channels.telegram.bot import make_voice_handler

    voice = MagicMock()
    voice.file_id = "file-1"
    update = _fake_telegram_update(voice=voice)

    file_obj = MagicMock()
    file_obj.download_as_bytearray = AsyncMock(return_value=bytearray(b"fake-audio"))
    context = MagicMock()
    context.bot.get_file = AsyncMock(return_value=file_obj)

    with patch("channels.telegram.bot.stt_service") as mock_stt, \
         patch("channels.telegram.bot.process_telegram_message") as mock_process:
        mock_stt.transcribe.return_value = "I need help"
        asyncio.run(make_voice_handler(company_id=1)(update, context))

    mock_process.assert_called_once()
    call_kwargs = mock_process.call_args.kwargs
    assert call_kwargs["text"] == "I need help"
    assert call_kwargs["source_type"] == "voice"


def test_telegram_photo_handler_describes_and_forwards_text():
    from channels.telegram.bot import make_photo_handler

    photo = MagicMock()
    photo.file_id = "file-2"
    update = _fake_telegram_update(photo=[photo], caption="check this out")

    file_obj = MagicMock()
    file_obj.download_as_bytearray = AsyncMock(return_value=bytearray(b"fake-image"))
    context = MagicMock()
    context.bot.get_file = AsyncMock(return_value=file_obj)

    with patch("channels.telegram.bot.vision_service") as mock_vision, \
         patch("channels.telegram.bot.process_telegram_message") as mock_process:
        mock_vision.describe_image.return_value = "A broken router"
        asyncio.run(make_photo_handler(company_id=1)(update, context))

    mock_process.assert_called_once()
    call_kwargs = mock_process.call_args.kwargs
    assert "A broken router" in call_kwargs["text"]
    assert "check this out" in call_kwargs["text"]
    assert call_kwargs["source_type"] == "image"
