"""Builds the ASGI application that serves the A2A endpoints."""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes, create_rest_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, TransportProtocol
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from a2a_bridge import __version__
from a2a_bridge.config import Settings
from a2a_bridge.conversations import ConversationStore
from a2a_bridge.directline import DirectLineClient
from a2a_bridge.executor import CopilotStudioAgentExecutor
from a2a_bridge.translate import ADAPTIVE_CARD

REST_PREFIX = "/rest"
PUBLIC_PATHS = frozenset({AGENT_CARD_WELL_KNOWN_PATH, "/healthz"})


def build_agent_card(settings: Settings) -> AgentCard:
    card = AgentCard(
        name=settings.agent_name,
        description=settings.agent_description,
        version=settings.agent_version,
        supported_interfaces=[
            AgentInterface(
                url=f"{settings.public_url}/",
                protocol_binding=TransportProtocol.JSONRPC.value,
                protocol_version="1.0",
            ),
            AgentInterface(
                url=f"{settings.public_url}{REST_PREFIX}",
                protocol_binding=TransportProtocol.HTTP_JSON.value,
                protocol_version="1.0",
            ),
        ],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "text/markdown", "application/json", ADAPTIVE_CARD],
        skills=[
            AgentSkill(
                id="chat",
                name=settings.skill_name,
                description=settings.skill_description,
                tags=list(settings.skill_tags),
                examples=list(settings.skill_examples),
            )
        ],
    )
    if settings.api_keys:
        card.security_schemes["bearer"].CopyFrom(
            SecurityScheme(
                http_auth_security_scheme=HTTPAuthSecurityScheme(
                    scheme="bearer", description="API key issued by the bridge operator."
                )
            )
        )
        card.security_requirements.append(
            SecurityRequirement(schemes={"bearer": StringList(list=[])})
        )
    return card


class ApiKeyMiddleware:
    """Require ``Authorization: Bearer <key>`` (or ``X-API-Key``) on everything but public paths."""

    def __init__(self, app: ASGIApp, keys: tuple[str, ...]) -> None:
        self.app = app
        self.keys = tuple(k.encode() for k in keys)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.keys or scope["path"] in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        auth = headers.get(b"authorization", b"")
        presented = (
            auth[7:].strip() if auth[:7].lower() == b"bearer " else headers.get(b"x-api-key", b"")
        )
        if not any(hmac.compare_digest(presented, key) for key in self.keys):
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_app(settings: Settings, http: httpx.AsyncClient | None = None) -> Starlette:
    """Create the bridge application. Pass ``http`` to inject a custom/mocked HTTP client."""
    client = DirectLineClient(settings, http)
    conversations = ConversationStore(
        client, ttl=settings.conversation_ttl, max_size=settings.max_conversations
    )
    card = build_agent_card(settings)
    handler = DefaultRequestHandler(
        agent_executor=CopilotStudioAgentExecutor(client, conversations),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse(
            {"status": "ok", "version": __version__, "conversations": len(conversations)}
        )

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        yield
        await client.aclose()

    routes = [
        Route("/healthz", healthz, methods=["GET"]),
        *create_agent_card_routes(card),
        *create_jsonrpc_routes(handler, rpc_url="/", enable_v0_3_compat=True),
        *create_rest_routes(handler, path_prefix=REST_PREFIX, enable_v0_3_compat=True),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    if settings.api_keys:
        app.add_middleware(ApiKeyMiddleware, keys=settings.api_keys)
    return app
