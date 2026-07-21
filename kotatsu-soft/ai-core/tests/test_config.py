import pytest

import config as config_module


def test_config_load_fails_when_required_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    with pytest.raises(config_module.ConfigError) as exc_info:
        config_module.Config.load()

    assert "DISCORD_TOKEN" in str(exc_info.value)


def test_config_load_fails_when_channel_id_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEETING_CHANNEL_ID", "not-an-int")

    with pytest.raises(config_module.ConfigError) as exc_info:
        config_module.Config.load()

    assert "MEETING_CHANNEL_ID" in str(exc_info.value)
