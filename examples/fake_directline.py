"""A tiny in-memory stand-in for the Direct Line API, for local demos and tests.

It behaves like a simple Copilot Studio agent:

* ``card``     -> replies with an Adaptive Card
* ``choices``  -> replies with suggested actions
* ``silent``   -> never replies (exercises the timeout path)
* ``count``    -> reports how many messages this conversation has seen
* anything else is echoed back, as two separate messages

Run it with::

    uv run uvicorn examples.fake_directline:app --port 3978

and point the bridge at it with ``DIRECTLINE_BASE_URL=http://localhost:3978/v3/directline``
and ``DIRECTLINE_SECRET=local-dev-secret``.
"""

from __future__ import annotations

import uuid
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

SECRET = "local-dev-secret"
BOT = {"id": "fake-copilot", "name": "Fake Copilot", "role": "bot"}

_tokens: dict[str, str] = {}  # token -> conversation id
_conversations: dict[str, list[dict[str, Any]]] = {}


def _auth(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:] if header.lower().startswith("bearer ") else None


def _deny() -> JSONResponse:
    return JSONResponse({"error": {"code": "BadArgument", "message": "Invalid token"}}, 403)


async def token_endpoint(_: Request) -> JSONResponse:
    """Mimics the Copilot Studio token endpoint (no auth, returns a bootstrap token)."""
    return JSONResponse({"token": SECRET, "expires_in": 3600})


async def start_conversation(request: Request) -> JSONResponse:
    if _auth(request) != SECRET:
        return _deny()
    conversation_id = uuid.uuid4().hex
    token = f"tok-{uuid.uuid4().hex}"
    _tokens[token] = conversation_id
    _conversations[conversation_id] = []
    return JSONResponse(
        {"conversationId": conversation_id, "token": token, "expires_in": 1800}, 201
    )


async def refresh_token(request: Request) -> JSONResponse:
    old = _auth(request)
    if old not in _tokens:
        return _deny()
    token = f"tok-{uuid.uuid4().hex}"
    _tokens[token] = _tokens.pop(old)
    return JSONResponse({"conversationId": _tokens[token], "token": token, "expires_in": 1800})


def _authorised(request: Request) -> list[dict[str, Any]] | None:
    conversation_id = request.path_params["conversation_id"]
    if _tokens.get(_auth(request) or "") != conversation_id:
        return None
    return _conversations.get(conversation_id)


def _reply(activity: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {"id": uuid.uuid4().hex, "from": BOT, "replyToId": activity["id"], **fields}


def _agent_turn(activity: dict[str, Any], log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    text = (activity.get("text") or "").strip()
    command = text.lower()
    if command == "silent":
        return []
    replies = [_reply(activity, type="typing")]
    if command == "card":
        card = {
            "type": "AdaptiveCard",
            "version": "1.5",
            "body": [{"type": "TextBlock", "text": "Here is your card"}],
        }
        replies.append(
            _reply(
                activity,
                type="message",
                attachments=[
                    {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}
                ],
            )
        )
    elif command == "choices":
        replies.append(
            _reply(
                activity,
                type="message",
                text="Pick one:",
                suggestedActions={
                    "actions": [
                        {"type": "imBack", "title": "Red", "value": "red"},
                        {"type": "imBack", "title": "Blue", "value": "blue"},
                    ]
                },
            )
        )
    elif command == "count":
        turns = sum(1 for a in log if a.get("from", {}).get("role") == "user")
        replies.append(_reply(activity, type="message", text=f"Messages so far: {turns}"))
    else:
        if activity.get("value") is not None:
            text = f"{text} (value: {activity['value']})".strip()
        replies.append(_reply(activity, type="message", text=f"You said: {text}"))
        replies.append(
            _reply(activity, type="message", text="**Anything else?**", textFormat="markdown")
        )
    return replies


async def post_activity(request: Request) -> JSONResponse:
    log = _authorised(request)
    if log is None:
        return _deny()
    activity = await request.json()
    activity["id"] = uuid.uuid4().hex
    log.append(activity)
    log.extend(_agent_turn(activity, log))
    return JSONResponse({"id": activity["id"]})


async def get_activities(request: Request) -> JSONResponse:
    log = _authorised(request)
    if log is None:
        return _deny()
    start = int(request.query_params.get("watermark") or 0)
    return JSONResponse({"activities": log[start:], "watermark": str(len(log))})


app = Starlette(
    routes=[
        Route("/token", token_endpoint, methods=["GET"]),
        Route("/v3/directline/conversations", start_conversation, methods=["POST"]),
        Route("/v3/directline/tokens/refresh", refresh_token, methods=["POST"]),
        Route(
            "/v3/directline/conversations/{conversation_id}/activities",
            post_activity,
            methods=["POST"],
        ),
        Route(
            "/v3/directline/conversations/{conversation_id}/activities",
            get_activities,
            methods=["GET"],
        ),
    ]
)
