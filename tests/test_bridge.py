"""End-to-end tests: A2A client -> bridge -> fake Direct Line."""

from __future__ import annotations

import time
import uuid

import httpx
import pytest
from a2a.client import ClientConfig, create_client
from a2a.helpers import get_text_parts, new_text_message
from a2a.types import Role, SendMessageRequest, TaskState

from a2a_bridge.app import build_agent_card
from a2a_bridge.config import Settings
from a2a_bridge.conversations import ConversationStore
from a2a_bridge.directline import DirectLineClient
from a2a_bridge.translate import ADAPTIVE_CARD, SUGGESTED_ACTIONS
from tests.conftest import with_changes


def _request(text: str, context_id: str | None = None) -> SendMessageRequest:
    message = new_text_message(text, context_id=context_id, role=Role.ROLE_USER)
    return SendMessageRequest(message=message)


async def _send(client, text: str, context_id: str | None = None):
    responses = [r async for r in client.send_message(_request(text, context_id))]
    assert len(responses) == 1
    return responses[0].task


def _texts(task) -> list[str]:
    return [t for artifact in task.artifacts for t in get_text_parts(artifact.parts)]


async def test_agent_card_is_public_and_complete(bridge: httpx.AsyncClient):
    card = (await bridge.get("/.well-known/agent-card.json")).json()
    assert card["name"] == "Test Agent"
    assert card["capabilities"]["streaming"] is True
    bindings = {i["protocolBinding"]: i["url"] for i in card["supportedInterfaces"]}
    assert bindings == {"JSONRPC": "http://bridge.test/", "HTTP+JSON": "http://bridge.test/rest"}
    assert card["skills"][0]["id"] == "chat"


async def test_healthz(bridge: httpx.AsyncClient):
    body = (await bridge.get("/healthz")).json()
    assert body["status"] == "ok"


async def test_send_message_returns_completed_task(a2a_client):
    task = await _send(await a2a_client(), "hello")
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert _texts(task) == ["You said: hello", "**Anything else?**"]
    assert task.artifacts[0].parts[1].media_type == "text/markdown"


async def test_streaming_emits_working_artifacts_and_completion(a2a_client):
    events = [
        r async for r in (await a2a_client(streaming=True)).send_message(_request("stream me"))
    ]
    kinds = [e.WhichOneof("payload") for e in events]
    assert kinds[0] == "task"
    assert kinds.count("artifact_update") == 2
    final = events[-1].status_update
    assert final.status.state == TaskState.TASK_STATE_COMPLETED
    chunks = [e.artifact_update for e in events if e.HasField("artifact_update")]
    assert [c.append for c in chunks] == [False, True]
    assert len({c.artifact.artifact_id for c in chunks}) == 1


async def test_context_id_keeps_the_same_conversation(a2a_client):
    client = await a2a_client()
    context_id = str(uuid.uuid4())
    await _send(client, "first", context_id)
    task = await _send(client, "count", context_id)
    assert _texts(task) == ["Messages so far: 2"]
    assert task.context_id == context_id

    fresh = await _send(client, "count")
    assert _texts(fresh) == ["Messages so far: 1"]


async def test_adaptive_cards_and_suggested_actions_are_data_parts(a2a_client):
    card_task = await _send(await a2a_client(), "card")
    [part] = card_task.artifacts[0].parts
    assert part.media_type == ADAPTIVE_CARD

    choices_task = await _send(await a2a_client(), "choices")
    parts = choices_task.artifacts[0].parts
    assert parts[0].text == "Pick one:"
    assert parts[1].media_type == SUGGESTED_ACTIONS


async def test_no_reply_fails_task_with_reason(a2a_client):
    task = await _send(await a2a_client(), "silent")
    assert task.status.state == TaskState.TASK_STATE_FAILED
    assert "No reply" in task.status.message.parts[0].text


async def test_token_endpoint_mode(settings: Settings, make_bridge):
    settings = with_changes(
        settings, directline_secret=None, directline_token_endpoint="http://directline.test/token"
    )
    async with make_bridge(settings) as http:
        client = await create_client(
            build_agent_card(settings),
            client_config=ClientConfig(streaming=False, httpx_client=http),
        )
        task = await _send(client, "via token endpoint")
    assert _texts(task)[0] == "You said: via token endpoint"


async def test_api_keys_protect_everything_but_the_card(settings: Settings, make_bridge):
    settings = with_changes(settings, api_keys=("k1", "k2"))
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "SendMessage",
        "params": {"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "hi"}]}},
    }
    headers = {"A2A-Version": "1.0"}
    async with make_bridge(settings) as http:
        card = (await http.get("/.well-known/agent-card.json")).json()
        assert "bearer" in card["securitySchemes"]
        assert (await http.get("/healthz")).status_code == 200
        assert (await http.post("/", json=body, headers=headers)).status_code == 401
        bad = {**headers, "Authorization": "Bearer nope"}
        assert (await http.post("/", json=body, headers=bad)).status_code == 401
        good = {**headers, "Authorization": "Bearer k2"}
        response = await http.post("/", json=body, headers=good)
        assert response.status_code == 200
        assert response.json()["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
        assert (await http.post("/", json=body, headers={**headers, "X-API-Key": "k1"})).is_success


async def test_rest_binding(bridge: httpx.AsyncClient):
    body = {"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "rest"}]}}
    response = await bridge.post("/rest/message:send", json=body, headers={"A2A-Version": "1.0"})
    assert response.status_code == 200, response.text
    assert response.json()["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


async def test_expiring_token_is_refreshed(settings: Settings, directline_http):
    client = DirectLineClient(settings, directline_http)
    store = ConversationStore(client, ttl=60, max_size=10)
    async with store.checkout("ctx") as conversation:
        old_token = conversation.token
        conversation.expires_at = time.monotonic() + 5  # inside the refresh margin
        replies = [a async for a in client.converse(conversation, "hi")]
    assert conversation.token != old_token
    assert replies[0]["text"] == "You said: hi"


@pytest.mark.parametrize("max_size", [1, 2])
async def test_store_is_bounded(settings: Settings, directline_http, max_size: int):
    store = ConversationStore(
        DirectLineClient(settings, directline_http), ttl=60, max_size=max_size
    )
    for i in range(5):
        async with store.checkout(f"ctx-{i}"):
            pass
    assert len(store) <= max_size


async def test_cancel_running_task(bridge: httpx.AsyncClient):
    headers = {"A2A-Version": "1.0"}
    body = {
        "message": {"messageId": "c1", "role": "ROLE_USER", "parts": [{"text": "silent"}]},
        "configuration": {"returnImmediately": True},
    }
    task = (await bridge.post("/rest/message:send", json=body, headers=headers)).json()["task"]
    response = await bridge.post(f"/rest/tasks/{task['id']}:cancel", json={}, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"]["state"] == "TASK_STATE_CANCELED"
