import pytest

from a2a_bridge.config import ConfigError, Settings, load_dotenv


def test_requires_a_credential():
    with pytest.raises(ConfigError, match="DIRECTLINE_SECRET or DIRECTLINE_TOKEN_ENDPOINT"):
        Settings.from_env({})


def test_rejects_both_credentials():
    with pytest.raises(ConfigError, match="only one"):
        Settings.from_env({"DIRECTLINE_SECRET": "s", "DIRECTLINE_TOKEN_ENDPOINT": "https://t"})


def test_parses_environment():
    settings = Settings.from_env(
        {
            "DIRECTLINE_SECRET": "s",
            "DIRECTLINE_BASE_URL": "https://europe.directline.botframework.com/v3/directline/",
            "BRIDGE_PORT": "9000",
            "BRIDGE_API_KEYS": "a | b",
            "AGENT_SKILL_TAGS": "hr|benefits",
            "AGENT_SKILL_EXAMPLES": "How many vacation days do I have?",
            "REPLY_TIMEOUT_SECONDS": "15",
        }
    )
    assert settings.directline_base_url.endswith("/v3/directline")
    assert settings.public_url == "http://localhost:9000"
    assert settings.api_keys == ("a", "b")
    assert settings.skill_tags == ("hr", "benefits")
    assert settings.skill_examples == ("How many vacation days do I have?",)
    assert settings.reply_timeout == 15.0


def test_rejects_bad_numbers():
    with pytest.raises(ConfigError, match="REPLY_TIMEOUT_SECONDS"):
        Settings.from_env({"DIRECTLINE_SECRET": "s", "REPLY_TIMEOUT_SECONDS": "soon"})


def test_load_dotenv_does_not_override(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text('# comment\nexport A2A_TEST_X="from-file"\nA2A_TEST_Y=kept\n')
    monkeypatch.setenv("A2A_TEST_Y", "from-env")
    monkeypatch.delenv("A2A_TEST_X", raising=False)
    load_dotenv(env_file)
    import os

    assert os.environ["A2A_TEST_X"] == "from-file"
    assert os.environ["A2A_TEST_Y"] == "from-env"
