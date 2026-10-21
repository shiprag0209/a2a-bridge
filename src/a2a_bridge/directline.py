"""Async client for the Bot Framework Direct Line 3.0 API used by Copilot Studio agents."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from a2a_bridge.config import Settings

logger = logging.getLogger(__name__)

# Refresh conversation tokens this many seconds before they expire.
_TOKEN_REFRESH_MARGIN = 120.0


class DirectLineError(RuntimeError):
    """Direct Line returned an error or an unexpected payload."""


class ReplyTimeoutError(DirectLineError):
    """The agent did not reply within the configured timeout."""


@dataclass
class Conversation:
    """A live Direct Line conversation, which maps to one A2A context."""

    conversation_id: str
    token: str
    expires_at: float
    user_id: str = field(default_factory=lambda: f"a2a-{uuid.uuid4().hex[:12]}")
    watermark: str | None = None


class DirectLineClient:
    """Minimal, dependency-light Direct Line client.

    Authentication follows Microsoft's recommended flow: the long-lived secret (or the Copilot
    Studio token endpoint) is used only to start a conversation; every later call uses the
    short-lived, conversation-scoped token returned by Direct Line.
    """

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        self._owns_http = http is None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # -- conversation lifecycle -------------------------------------------------------------

    async def start_conversation(self) -> Conversation:
        bootstrap = await self._bootstrap_token()
        data = await self._request("POST", "/conversations", token=bootstrap)
        return Conversation(
            conversation_id=_require(data, "conversationId"),
            token=data.get("token") or bootstrap,
            expires_at=time.monotonic() + float(data.get("expires_in", 1800)),
        )

    async def ensure_fresh(self, conversation: Conversation) -> None:
        """Refresh the conversation token if it is about to expire."""
        if conversation.expires_at - time.monotonic() > _TOKEN_REFRESH_MARGIN:
            return
        data = await self._request("POST", "/tokens/refresh", token=conversation.token)
        conversation.token = _require(data, "token")
        conversation.expires_at = time.monotonic() + float(data.get("expires_in", 1800))
        logger.debug("Refreshed token for conversation %s", conversation.conversation_id)

    # -- messaging --------------------------------------------------------------------------

    async def send(self, conversation: Conversation, text: str, value: Any | None = None) -> str:
        """Post a user message and return the Direct Line activity id."""
        await self.ensure_fresh(conversation)
        activity: dict[str, Any] = {
            "type": "message",
            "from": {"id": conversation.user_id, "role": "user"},
            "locale": self._settings.locale,
            "text": text,
        }
        if value is not None:
            activity["value"] = value
        data = await self._request(
            "POST",
            f"/conversations/{conversation.conversation_id}/activities",
            token=conversation.token,
            json=activity,
        )
        return _require(data, "id")

    async def fetch(self, conversation: Conversation) -> list[dict[str, Any]]:
        """Fetch activities newer than the conversation's watermark."""
        params = {"watermark": conversation.watermark} if conversation.watermark else None
        data = await self._request(
            "GET",
            f"/conversations/{conversation.conversation_id}/activities",
            token=conversation.token,
            params=params,
        )
        if data.get("watermark") is not None:
            conversation.watermark = str(data["watermark"])
        return list(data.get("activities") or [])

    async def converse(
        self, conversation: Conversation, text: str, value: Any | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Send one user turn and yield each agent ``message`` activity as it arrives.

        Copilot Studio has no explicit "turn finished" marker, so a turn ends when the agent
        sends ``endOfConversation`` or has been silent for ``idle_timeout`` seconds after its
        first reply. ``typing`` activities count as activity and keep the turn open.
        """
        s = self._settings
        activity_id = await self.send(conversation, text, value)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + s.reply_timeout
        last_seen: float | None = None
        replied = False

        while True:
            ended = False
            for activity in await self.fetch(conversation):
                if (activity.get("from") or {}).get("id") == conversation.user_id:
                    continue
                reply_to = activity.get("replyToId")
                if reply_to and reply_to != activity_id:
                    continue  # late reply to an earlier turn
                kind = activity.get("type")
                last_seen = loop.time()
                if kind == "endOfConversation":
                    ended = True
                elif kind == "message":
                    replied = True
                    yield activity

            now = loop.time()
            if ended or (replied and last_seen is not None and now - last_seen >= s.idle_timeout):
                return
            if now >= deadline:
                if replied:
                    return
                raise ReplyTimeoutError(
                    f"No reply from the Copilot Studio agent within {s.reply_timeout:g}s"
                )
            await asyncio.sleep(s.poll_interval)

    # -- internals --------------------------------------------------------------------------

    async def _bootstrap_token(self) -> str:
        if self._settings.directline_secret:
            return self._settings.directline_secret
        endpoint = self._settings.directline_token_endpoint
        assert endpoint is not None  # guaranteed by Settings validation
        try:
            response = await self._http.get(endpoint)
            response.raise_for_status()
            return _require(response.json(), "token")
        except httpx.HTTPError as exc:
            raise DirectLineError(f"Could not get a token from the token endpoint: {exc}") from exc

    async def _request(
        self, method: str, path: str, *, token: str, **kwargs: Any
    ) -> dict[str, Any]:
        url = f"{self._settings.directline_base_url}{path}"
        try:
            response = await self._http.request(
                method, url, headers={"Authorization": f"Bearer {token}"}, **kwargs
            )
        except httpx.HTTPError as exc:
            raise DirectLineError(f"Direct Line request failed: {exc}") from exc
        if response.status_code >= 400:
            raise DirectLineError(
                f"Direct Line {method} {path} returned {response.status_code}: "
                f"{_error_message(response)}"
            )
        return response.json() if response.content else {}


def _require(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not value:
        raise DirectLineError(f"Direct Line response is missing '{key}'")
    return str(value)


def _error_message(response: httpx.Response) -> str:
    try:
        error = response.json().get("error") or {}
        return error.get("message") or error.get("code") or response.text[:200]
    except ValueError:
        return response.text[:200]
