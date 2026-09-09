"""Tests for voice replies (text-to-speech).

The property that matters, the same as test_mailer.py for email: a voice
reply that is supposedly "on" must never silently lose the customer's answer.
`send_voice_reply_if_enabled` returns `None` — never raises — for every
reason a voice reply does not apply, and the caller's existing text send is
what runs instead. These tests are the list of those reasons, plus the happy
path where everything is actually configured.
"""

from __future__ import annotations

import sys

import pytest

from database.manager import DatabaseManager


def _wire(platform, monkeypatch):
    import database.manager as manager_module

    test_manager = platform["manager"]
    monkeypatch.setattr(manager_module, "database_manager", test_manager)
    for module in list(sys.modules.values()):
        held = getattr(module, "database_manager", None)
        if isinstance(held, DatabaseManager) and held is not test_manager:
            monkeypatch.setattr(module, "database_manager", test_manager)
    return test_manager


def _enable_voice_reply(company_id: int) -> None:
    from backend.services.company_settings_service import company_settings_service

    company_settings_service.update_section(
        company_id, "ai_behavior", {"voice_reply_enabled": True}, None
    )


class FakeProvider:
    name = "fake"

    def __init__(self, *, configured=True, audio=b"FAKE-AUDIO", error=None):
        self._configured = configured
        self.audio = audio
        self.error = error
        self.calls: list[str] = []

    def is_configured(self):
        return self._configured

    def synthesize(self, *, text):
        self.calls.append(text)
        if self.error:
            raise self.error
        return self.audio


@pytest.fixture()
def uploads(monkeypatch, tmp_path):
    from config.settings import config

    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads", raising=False)


@pytest.fixture()
def wired(platform, monkeypatch, uploads):
    _wire(platform, monkeypatch)
    from config.settings import config

    monkeypatch.setattr(config, "APP_PUBLIC_URL", "https://app.example.com")
    return platform


# ----------------------------------------------------------------------
# voice_status
# ----------------------------------------------------------------------


def test_voice_status_names_the_missing_key(monkeypatch):
    from backend.services import tts_service
    from config.settings import config

    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(tts_service.tts_service, "provider", tts_service.NullProvider())

    status = tts_service.tts_service.voice_status()

    assert status["configured"] is False
    assert "OPENAI_API_KEY" in status["missing"]


def test_voice_status_is_configured_when_the_provider_is(monkeypatch):
    from backend.services import tts_service
    from config.settings import config

    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(tts_service.tts_service, "provider", FakeProvider())

    status = tts_service.tts_service.voice_status()

    assert status["configured"] is True
    assert status["missing"] == []


# ----------------------------------------------------------------------
# synthesize_and_store
# ----------------------------------------------------------------------


def test_synthesis_refuses_with_no_provider_configured(monkeypatch):
    from backend.services import tts_service

    monkeypatch.setattr(
        tts_service.tts_service, "provider", FakeProvider(configured=False)
    )

    with pytest.raises(tts_service.TTSNotConfiguredError):
        tts_service.tts_service.synthesize_and_store(company_id=1, text="hello")


def test_synthesis_refuses_blank_text(monkeypatch):
    from backend.services import tts_service

    monkeypatch.setattr(tts_service.tts_service, "provider", FakeProvider())

    with pytest.raises(tts_service.TTSError):
        tts_service.tts_service.synthesize_and_store(company_id=1, text="   ")


def test_synthesis_stores_the_audio_and_returns_a_url(monkeypatch, uploads):
    from backend.services import tts_service

    provider = FakeProvider(audio=b"MP3-BYTES")
    monkeypatch.setattr(tts_service.tts_service, "provider", provider)

    result = tts_service.tts_service.synthesize_and_store(
        company_id=42, text="Hi there"
    )

    assert result["media_type"] == "audio"
    assert result["url"].startswith("/api/media/42/")
    assert provider.calls == ["Hi there"]


def test_synthesis_truncates_text_longer_than_the_providers_limit(
    monkeypatch, uploads
):
    from backend.services import tts_service

    provider = FakeProvider()
    monkeypatch.setattr(tts_service.tts_service, "provider", provider)

    tts_service.tts_service.synthesize_and_store(
        company_id=1, text="x" * (tts_service.MAX_TEXT_LENGTH + 500)
    )

    assert len(provider.calls[0]) == tts_service.MAX_TEXT_LENGTH


# ----------------------------------------------------------------------
# send_voice_reply_if_enabled
# ----------------------------------------------------------------------


def test_voice_reply_is_off_by_default(wired, alpha, monkeypatch):
    """A company that has never touched this setting gets text, exactly as it
    always has — the setting shipping did not change anyone's behaviour."""
    from backend.services import tts_service

    monkeypatch.setattr(tts_service.tts_service, "provider", FakeProvider())

    result = tts_service.send_voice_reply_if_enabled(
        company_id=alpha["id"], channel="messenger", recipient_id="c1", text="hi"
    )

    assert result is None


def test_voice_reply_stays_off_with_no_provider_configured(wired, alpha, monkeypatch):
    from backend.services import tts_service

    _enable_voice_reply(alpha["id"])
    monkeypatch.setattr(
        tts_service.tts_service, "provider", FakeProvider(configured=False)
    )

    result = tts_service.send_voice_reply_if_enabled(
        company_id=alpha["id"], channel="messenger", recipient_id="c1", text="hi"
    )

    assert result is None


def test_voice_reply_is_skipped_when_buttons_are_attached(wired, alpha, monkeypatch):
    """Messenger's attachment payload carries no buttons — sending one would
    silently drop the quick replies the reply actually needs."""
    from backend.services import tts_service

    _enable_voice_reply(alpha["id"])
    monkeypatch.setattr(tts_service.tts_service, "provider", FakeProvider())

    result = tts_service.send_voice_reply_if_enabled(
        company_id=alpha["id"],
        channel="messenger",
        recipient_id="c1",
        text="hi",
        buttons=["Yes", "No"],
    )

    assert result is None


def test_voice_reply_stays_off_with_no_public_url(platform, monkeypatch, alpha, uploads):
    _wire(platform, monkeypatch)
    from backend.services import tts_service
    from config.settings import config

    monkeypatch.setattr(config, "APP_PUBLIC_URL", "")
    _enable_voice_reply(alpha["id"])
    monkeypatch.setattr(tts_service.tts_service, "provider", FakeProvider())

    result = tts_service.send_voice_reply_if_enabled(
        company_id=alpha["id"], channel="messenger", recipient_id="c1", text="hi"
    )

    assert result is None


def test_voice_reply_falls_back_to_none_when_synthesis_fails(wired, alpha, monkeypatch):
    from backend.services import tts_service

    _enable_voice_reply(alpha["id"])
    monkeypatch.setattr(
        tts_service.tts_service,
        "provider",
        FakeProvider(error=tts_service.TTSError("boom")),
    )

    result = tts_service.send_voice_reply_if_enabled(
        company_id=alpha["id"], channel="messenger", recipient_id="c1", text="hi"
    )

    assert result is None


def test_voice_reply_sends_audio_when_everything_is_configured(
    wired, alpha, monkeypatch
):
    from backend.services import tts_service
    import channels.sender as sender_module

    _enable_voice_reply(alpha["id"])
    monkeypatch.setattr(tts_service.tts_service, "provider", FakeProvider())

    captured: dict = {}

    def fake_send_media(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "channel": kwargs["channel"],
            "recipient_id": kwargs["recipient_id"],
        }

    monkeypatch.setattr(sender_module, "send_media", fake_send_media)

    result = tts_service.send_voice_reply_if_enabled(
        company_id=alpha["id"],
        channel="messenger",
        recipient_id="c1",
        text="Hello there",
    )

    assert result["ok"] is True
    assert result["media_type"] == "audio"
    assert result["media_url"].startswith("/api/media/")
    assert captured["media_type"] == "audio"
    assert captured["media_url"].startswith("https://app.example.com/api/media/")


def test_one_companys_voice_setting_does_not_turn_on_another_companys(
    wired, alpha, beta, monkeypatch
):
    from backend.services import tts_service

    _enable_voice_reply(alpha["id"])
    monkeypatch.setattr(tts_service.tts_service, "provider", FakeProvider())

    result = tts_service.send_voice_reply_if_enabled(
        company_id=beta["id"], channel="messenger", recipient_id="c1", text="hi"
    )

    assert result is None
