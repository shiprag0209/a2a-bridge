from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest
from a2a.client import Client, ClientConfig, create_client

from a2a_bridge.app import build_agent_card, create_app
from a2a_bridge.config import Settings
from examples import fake_directline

BRIDGE_URL = "http://bridge.test"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        directline_secret=fake_directline.SECRET,
        directline_base_url="http://directline.test/v3/directline",
        public_url=BRIDGE_URL,
        agent_name="Test Agent",
        reply_timeout=1.0,
        idle_timeout=0.05,
        poll_interval=0.01,
    )


@pytest.fixture
def directline_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake_directline.app), base_url="http://directline.test"
    )


@pytest.fixture
def make_bridge(directline_http: httpx.AsyncClient) -> Callable[..., httpx.AsyncClient]:
    def factory(settings: Settings, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
        app = create_app(settings, http=directline_http)
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BRIDGE_URL, headers=headers
        )

    return factory


@pytest.fixture
async def bridge(settings: Settings, make_bridge) -> AsyncIterator[httpx.AsyncClient]:
    async with make_bridge(settings) as http:
        yield http


@pytest.fixture
def a2a_client(settings: Settings, bridge: httpx.AsyncClient) -> Callable[..., Awaitable[Client]]:
    async def factory(streaming: bool = False) -> Client:
        return await create_client(
            build_agent_card(settings),
            client_config=ClientConfig(streaming=streaming, httpx_client=bridge),
        )

    return factory


def with_changes(settings: Settings, **changes) -> Settings:
    return dataclasses.replace(settings, **changes)
