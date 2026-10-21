"""Talk to a running a2a-bridge with the official A2A Python client.

    uv run python examples/client.py "Hello!"                      # one message
    uv run python examples/client.py --stream "Tell me a story"     # stream the reply
    uv run python examples/client.py --url http://localhost:8000 --api-key KEY "Hi"

Messages sent in one run share a context, so the agent remembers the conversation:

    uv run python examples/client.py "My name is Sam" "What is my name?"
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_text_parts, new_text_message
from a2a.types import Role, SendMessageRequest, TaskState


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("messages", nargs="+", help="one or more messages to send, in order")
    parser.add_argument("--url", default="http://localhost:8000", help="bridge base URL")
    parser.add_argument("--api-key", help="bridge API key, if BRIDGE_API_KEYS is set")
    parser.add_argument("--stream", action="store_true", help="stream the agent's reply")
    args = parser.parse_args()

    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else None
    async with httpx.AsyncClient(headers=headers, timeout=120) as http:
        card = await A2ACardResolver(http, args.url).get_agent_card()
        print(f"Connected to: {card.name} - {card.description}\n")
        client = await create_client(
            card, client_config=ClientConfig(streaming=args.stream, httpx_client=http)
        )
        context_id = str(uuid.uuid4())
        for text in args.messages:
            print(f"> {text}")
            message = new_text_message(text, context_id=context_id, role=Role.ROLE_USER)
            async for event in client.send_message(SendMessageRequest(message=message)):
                if event.HasField("artifact_update"):
                    for line in get_text_parts(event.artifact_update.artifact.parts):
                        print(line)
                elif event.HasField("task"):
                    task = event.task
                    for artifact in task.artifacts:
                        for line in get_text_parts(artifact.parts):
                            print(line)
                    if task.status.state == TaskState.TASK_STATE_FAILED:
                        print("! failed:", " ".join(get_text_parts(task.status.message.parts)))
                elif event.HasField("status_update"):
                    status = event.status_update.status
                    if status.state == TaskState.TASK_STATE_FAILED:
                        print("! failed:", " ".join(get_text_parts(status.message.parts)))
            print()


if __name__ == "__main__":
    asyncio.run(main())
