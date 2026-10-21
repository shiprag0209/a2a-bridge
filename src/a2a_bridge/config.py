"""Runtime configuration, read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DIRECTLINE_URL = "https://directline.botframework.com/v3/directline"


class ConfigError(ValueError):
    """Raised when the bridge is misconfigured."""


def load_dotenv(path: str | os.PathLike[str] = ".env") -> None:
    """Load KEY=VALUE lines from ``path`` into ``os.environ`` without overriding existing values."""
    file = Path(path)
    if not file.is_file():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _split(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split("|") if item.strip()]


def _float(env: dict[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw in (None, ""):
        return default
    try:
        number = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc
    if number <= 0:
        raise ConfigError(f"{key} must be greater than 0, got {raw!r}")
    return number


@dataclass(frozen=True)
class Settings:
    """All tunables for the bridge. See ``.env.example`` for documentation of each value."""

    # Copilot Studio / Direct Line
    directline_secret: str | None = None
    directline_token_endpoint: str | None = None
    directline_base_url: str = DEFAULT_DIRECTLINE_URL
    locale: str = "en-US"

    # A2A server
    host: str = "0.0.0.0"
    port: int = 8000
    public_url: str = "http://localhost:8000"
    api_keys: tuple[str, ...] = ()

    # Agent card
    agent_name: str = "Copilot Studio Agent"
    agent_description: str = "A Microsoft Copilot Studio agent exposed over the A2A protocol."
    agent_version: str = "1.0.0"
    skill_name: str = "Chat"
    skill_description: str = "Have a multi-turn conversation with the Copilot Studio agent."
    skill_tags: tuple[str, ...] = ("copilot-studio", "chat")
    skill_examples: tuple[str, ...] = field(default_factory=tuple)

    # Behaviour
    reply_timeout: float = 60.0
    idle_timeout: float = 2.0
    poll_interval: float = 0.5
    conversation_ttl: float = 1800.0
    max_conversations: int = 10_000
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if not (self.directline_secret or self.directline_token_endpoint):
            raise ConfigError(
                "Set either DIRECTLINE_SECRET or DIRECTLINE_TOKEN_ENDPOINT so the bridge can "
                "reach your Copilot Studio agent."
            )
        if self.directline_secret and self.directline_token_endpoint:
            raise ConfigError("Set only one of DIRECTLINE_SECRET or DIRECTLINE_TOKEN_ENDPOINT.")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if env is None else env)
        try:
            port = int(env.get("BRIDGE_PORT", "8000"))
            max_conversations = int(env.get("MAX_CONVERSATIONS", "10000"))
        except ValueError as exc:
            raise ConfigError(f"Invalid integer setting: {exc}") from exc
        tags = _split(env.get("AGENT_SKILL_TAGS"))
        return cls(
            directline_secret=env.get("DIRECTLINE_SECRET") or None,
            directline_token_endpoint=env.get("DIRECTLINE_TOKEN_ENDPOINT") or None,
            directline_base_url=(env.get("DIRECTLINE_BASE_URL") or DEFAULT_DIRECTLINE_URL).rstrip(
                "/"
            ),
            locale=env.get("AGENT_LOCALE", "en-US"),
            host=env.get("BRIDGE_HOST", "0.0.0.0"),
            port=port,
            public_url=(env.get("BRIDGE_PUBLIC_URL") or f"http://localhost:{port}").rstrip("/"),
            api_keys=tuple(_split(env.get("BRIDGE_API_KEYS"))),
            agent_name=env.get("AGENT_NAME") or cls.agent_name,
            agent_description=env.get("AGENT_DESCRIPTION") or cls.agent_description,
            agent_version=env.get("AGENT_VERSION") or cls.agent_version,
            skill_name=env.get("AGENT_SKILL_NAME") or cls.skill_name,
            skill_description=env.get("AGENT_SKILL_DESCRIPTION") or cls.skill_description,
            skill_tags=tuple(tags) if tags else cls.skill_tags,
            skill_examples=tuple(_split(env.get("AGENT_SKILL_EXAMPLES"))),
            reply_timeout=_float(env, "REPLY_TIMEOUT_SECONDS", 60.0),
            idle_timeout=_float(env, "IDLE_TIMEOUT_SECONDS", 2.0),
            poll_interval=_float(env, "POLL_INTERVAL_SECONDS", 0.5),
            conversation_ttl=_float(env, "CONVERSATION_TTL_SECONDS", 1800.0),
            max_conversations=max_conversations,
            log_level=env.get("LOG_LEVEL", "INFO").upper(),
        )
