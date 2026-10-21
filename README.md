# a2a-bridge

[![CI](https://github.com/shiprag0209/a2a-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/shiprag0209/a2a-bridge/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)
![A2A](https://img.shields.io/badge/A2A%20protocol-1.0%20%2B%200.3%20compat-6f42c1)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**Turn any Microsoft Copilot Studio agent into a standards-compliant [A2A (Agent2Agent)](https://a2a-protocol.org) agent, with no changes to the agent.**

`a2a-bridge` is a small, production-ready server. On one side it speaks the open A2A protocol, so any A2A client or orchestrator can discover and call your agent: LangGraph, CrewAI, Google ADK, Semantic Kernel, the A2A Inspector, or another agent platform. On the other side it talks to Copilot Studio through Microsoft's **Direct Line** channel, the same channel the Copilot Studio web chat uses.

```
 ┌──────────────────┐  A2A (JSON-RPC / REST, SSE)  ┌──────────────┐  Direct Line 3.0  ┌──────────────────────┐
 │  Any A2A client  │ ───────────────────────────▶ │  a2a-bridge  │ ────────────────▶ │ Copilot Studio agent │
 │ (agent, app, UI) │ ◀─────────────────────────── │              │ ◀──────────────── │ (topics, knowledge,  │
 └──────────────────┘  Agent Card · Tasks · Streams└──────────────┘   conversations   │  actions, flows)     │
                                                                                     └──────────────────────┘
```

---

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Quick start (2 minutes, no Copilot Studio needed)](#quick-start-2-minutes-no-copilot-studio-needed)
- [Connect your Copilot Studio agent](#connect-your-copilot-studio-agent)
- [Run it](#run-it)
- [Call the agent](#call-the-agent)
- [Configuration reference](#configuration-reference)
- [Message mapping](#message-mapping)
- [Security](#security)
- [Deploying to production](#deploying-to-production)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [FAQ](#faq)
- [License](#license)

---

## Features

| | |
|---|---|
| **A2A 1.0 compliant** | Built on the official [`a2a-sdk`](https://pypi.org/project/a2a-sdk/). Serves an Agent Card at `/.well-known/agent-card.json` and supports sending messages (blocking and streaming), getting, listing and cancelling tasks, and subscribing to tasks. |
| **Two transports** | JSON-RPC at `/` and HTTP+JSON (REST) at `/rest`, both advertised in the Agent Card. A2A **0.3** clients also work through the SDK's compatibility layer. |
| **Real streaming** | Each Copilot Studio message is streamed to the caller as a Server-Sent Event the moment it arrives. |
| **Multi-turn memory** | One A2A `contextId` = one Copilot Studio conversation, so topic flow, variables and history carry across messages. |
| **Rich content** | Markdown, Adaptive Cards, file and image attachments, and suggested actions come back as typed A2A parts. Structured input (`data` parts) reaches the agent as `Activity.Value`. |
| **Secure by default** | The Direct Line secret never leaves the server. Conversations run on short-lived, auto-refreshed tokens. Optional API-key auth uses constant-time comparison and is declared in the Agent Card. |
| **Operable** | `/healthz`, a non-root Docker image with a health check, structured logging, bounded memory (LRU + TTL), and clear failure reasons on tasks. |
| **Small and tested** | A few hundred lines of typed Python, tested end to end against a bundled fake Direct Line service. CI runs on Python 3.10 to 3.13. |

## How it works

1. A caller fetches the **Agent Card** and sends an A2A message.
2. The bridge looks up (or starts) the Direct Line **conversation** for the message's `contextId`.
3. The user's text is posted to Copilot Studio as a Direct Line `message` activity.
4. The bridge polls the conversation, filters for the agent's replies to *this* turn, and **streams each reply** back as a chunk of one `response` artifact.
5. Copilot Studio has no explicit "turn finished" signal. The turn ends when the agent sends `endOfConversation`, or when it has been quiet for `IDLE_TIMEOUT_SECONDS` after replying. The task is then marked **completed**.
6. If the agent never replies within `REPLY_TIMEOUT_SECONDS`, or Direct Line returns an error, the task is marked **failed** with a readable reason.

A deeper walkthrough is in [docs/architecture.md](docs/architecture.md).

---

## Quick start (2 minutes, no Copilot Studio needed)

The repo includes a fake Direct Line service, so you can see the whole flow locally before you connect a real agent.

**Prerequisites:** Python 3.10+ and [uv](https://docs.astral.sh/uv/getting-started/installation/) (or plain `pip`).

```bash
git clone https://github.com/shiprag0209/a2a-bridge.git
cd a2a-bridge
uv sync
```

**Terminal 1** starts the fake Copilot Studio:

```bash
uv run uvicorn examples.fake_directline:app --port 3978
```

**Terminal 2** starts the bridge, pointed at it:

```bash
DIRECTLINE_SECRET=local-dev-secret \
DIRECTLINE_BASE_URL=http://localhost:3978/v3/directline \
uv run a2a-bridge
```

**Terminal 3** talks to it over A2A:

```bash
uv run python examples/client.py "Hello there" count
```

```
Connected to: Copilot Studio Agent - A Microsoft Copilot Studio agent exposed over the A2A protocol.

> Hello there
You said: Hello there
**Anything else?**

> count
Messages so far: 2
```

`count` returns 2 because both messages went to the same conversation. Also try `card`, `choices` and `--stream`.

---

## Connect your Copilot Studio agent

You need **one** of two credentials from Copilot Studio. Use Option A unless you have a reason not to.

### Option A: Web channel security secret (recommended)

1. Open your agent in [Copilot Studio](https://copilotstudio.microsoft.com).
2. Go to **Settings → Security → Web channel security**.
3. Copy **Secret 1**. If you want to block anonymous web-chat access, turn on **Require secured access**. The bridge keeps working, because it uses the secret.
4. Set `DIRECTLINE_SECRET=<that secret>`.

### Option B: Token endpoint

Use this for agents that don't require secured access. The bridge fetches a fresh token from the endpoint for every new conversation, so no secret is stored.

1. Go to **Channels → Mobile app** (or **Web app**) in your agent.
2. Copy the **Token Endpoint** URL.
3. Set `DIRECTLINE_TOKEN_ENDPOINT=<that url>`.

### Pick the right region

If your Power Platform environment is outside the US, use the matching regional Direct Line endpoint for lower latency and data residency, for example:

```bash
DIRECTLINE_BASE_URL=https://europe.directline.botframework.com/v3/directline
```

To look up the exact URL, call
`https://<your-env>.environment.api.powerplatform.com/powervirtualagents/regionalchannelsettings?api-version=2022-03-01-preview`
and read `channelUrlsById.directline`. Then append `/v3/directline`.

### Make sure the agent is published

Direct Line always talks to the **published** version of your agent. After you change topics or knowledge, select **Publish** in Copilot Studio.

---

## Run it

### With uv / pip

```bash
cp .env.example .env        # then fill in DIRECTLINE_SECRET (or DIRECTLINE_TOKEN_ENDPOINT)
uv run a2a-bridge           # or: pip install . && a2a-bridge
```

```
a2a-bridge [--env-file .env] [--host 0.0.0.0] [--port 8000] [--version]
```

The bridge reads `.env` from the working directory. Real environment variables always take precedence over the file.

### With Docker

```bash
docker build -t a2a-bridge .
docker run --rm -p 8000:8000 --env-file .env a2a-bridge
```

The image runs as a non-root user and has a built-in `HEALTHCHECK` against `/healthz`.

### Check it's up

```bash
curl http://localhost:8000/healthz
curl http://localhost:8000/.well-known/agent-card.json
```

---

## Call the agent

### Python (official A2A SDK)

```python
import asyncio, httpx, uuid
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_text_parts, new_text_message
from a2a.types import Role, SendMessageRequest


async def main():
    async with httpx.AsyncClient(timeout=120) as http:
        card = await A2ACardResolver(http, "http://localhost:8000").get_agent_card()
        client = await create_client(
            card, client_config=ClientConfig(streaming=False, httpx_client=http)
        )

        context_id = str(uuid.uuid4())  # reuse it to continue the same conversation
        msg = new_text_message(
            "What can you help me with?", context_id=context_id, role=Role.ROLE_USER
        )
        async for event in client.send_message(SendMessageRequest(message=msg)):
            for artifact in event.task.artifacts:
                print("\n".join(get_text_parts(artifact.parts)))


asyncio.run(main())
```

A complete CLI version that also streams is in [`examples/client.py`](examples/client.py).

### curl: JSON-RPC

```bash
curl -s http://localhost:8000/ \
  -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' \
  -d '{
    "jsonrpc": "2.0", "id": 1, "method": "SendMessage",
    "params": { "message": {
      "messageId": "1", "role": "ROLE_USER", "contextId": "demo-1",
      "parts": [{ "text": "Hello!" }]
    }}
  }'
```

### curl: REST

```bash
curl -s http://localhost:8000/rest/message:send \
  -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' \
  -d '{"message":{"messageId":"1","role":"ROLE_USER","parts":[{"text":"Hello!"}]}}'
```

### curl: streaming (SSE)

```bash
curl -N http://localhost:8000/rest/message:stream \
  -H 'Content-Type: application/json' -H 'A2A-Version: 1.0' \
  -d '{"message":{"messageId":"1","role":"ROLE_USER","parts":[{"text":"Hello!"}]}}'
```

The stream sends the `Task` first, then a `working` status, then one `artifactUpdate` per agent message, and finally a `completed` (or `failed`) status.

### From other frameworks and agents

Any A2A-capable framework can use the bridge. Point it at the base URL, for example `http://localhost:8000`, and it discovers everything else from the Agent Card. Any platform that can add a remote A2A agent can call your Copilot Studio agent this way, including another Copilot Studio agent.

### Validate with the A2A Inspector

Run the [A2A Inspector](https://github.com/a2aproject/a2a-inspector) and connect it to `http://localhost:8000`. It checks the Agent Card, and you can chat with the agent from its UI.

---

## Configuration reference

All settings are environment variables (or `.env` entries). The annotated template is [`.env.example`](.env.example).

### Copilot Studio connection

| Variable | Default | Description |
|---|---|---|
| `DIRECTLINE_SECRET` | – | Web channel security secret. **Set this or `DIRECTLINE_TOKEN_ENDPOINT`, not both.** |
| `DIRECTLINE_TOKEN_ENDPOINT` | – | Copilot Studio token endpoint URL. |
| `DIRECTLINE_BASE_URL` | `https://directline.botframework.com/v3/directline` | Direct Line API base. Use your regional endpoint. |
| `AGENT_LOCALE` | `en-US` | Locale sent with each message. Copilot Studio uses it to pick the language. |

### A2A server

| Variable | Default | Description |
|---|---|---|
| `BRIDGE_HOST` | `0.0.0.0` | Bind address. |
| `BRIDGE_PORT` | `8000` | Bind port. |
| `BRIDGE_PUBLIC_URL` | `http://localhost:<port>` | External URL of the bridge, published in the Agent Card. **Set this when you deploy.** |
| `BRIDGE_API_KEYS` | *(empty = no auth)* | Pipe-separated API keys, e.g. `key-a\|key-b`. More than one key allows rotation without downtime. |

### Agent Card

| Variable | Default | Description |
|---|---|---|
| `AGENT_NAME` | `Copilot Studio Agent` | Display name other agents see. |
| `AGENT_DESCRIPTION` | *generic* | What the agent does. Orchestrators use this to decide when to call it, so be specific. |
| `AGENT_VERSION` | `1.0.0` | Version string in the card. |
| `AGENT_SKILL_NAME` | `Chat` | Name of the single advertised skill. |
| `AGENT_SKILL_DESCRIPTION` | *generic* | Skill description. |
| `AGENT_SKILL_TAGS` | `copilot-studio\|chat` | Pipe-separated tags. |
| `AGENT_SKILL_EXAMPLES` | *(none)* | Pipe-separated example prompts. |

### Behaviour

| Variable | Default | Description |
|---|---|---|
| `REPLY_TIMEOUT_SECONDS` | `60` | Max wait for the agent's first reply before the task fails. Raise it for slow generative answers or long-running actions. |
| `IDLE_TIMEOUT_SECONDS` | `2` | Silence after a reply that ends the turn. Raise it if long answers get cut off. Lower it for snappier replies. |
| `POLL_INTERVAL_SECONDS` | `0.5` | How often Direct Line is polled during a turn. |
| `CONVERSATION_TTL_SECONDS` | `1800` | Idle conversations are forgotten after this long. A later message in that context starts a new Copilot Studio conversation. |
| `MAX_CONVERSATIONS` | `10000` | Upper bound on tracked conversations (least recently used are evicted first). |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. |

---

## Message mapping

### A2A → Copilot Studio

| A2A input | Sent to Copilot Studio as |
|---|---|
| `text` parts | Activity `text` (several parts are joined with newlines) |
| `data` parts | Activity `value`. One part is sent as the object, several as a list. Read it in a topic with `Activity.Value`. |
| `contextId` | Selects the Direct Line conversation (created on first use) |
| file / url parts | Ignored (Direct Line text turns don't carry files) |

### Copilot Studio → A2A

| Copilot Studio output | Returned as a part in the `response` artifact |
|---|---|
| Plain text message | `text`, `mediaType: text/plain` |
| Markdown message | `text`, `mediaType: text/markdown` |
| Adaptive Card | `data` (the card JSON), `mediaType: application/vnd.microsoft.card.adaptive` |
| Other card / attachment with inline content | `data` or `text` with the attachment's content type |
| File / image attachment | `url` with `filename` and `mediaType` |
| Suggested actions (quick replies) | `data` `{"suggestedActions":[{"title","value"}]}`, `mediaType: application/vnd.a2a-bridge.suggested-actions+json` |
| `typing` | Not returned. It keeps the turn open. |

Each Copilot Studio message becomes one artifact chunk (`append: true` after the first), so streaming clients see answers progressively and non-streaming clients get the full artifact in the final task.

---

## Security

- **Keep the Direct Line secret on the server.** The bridge uses it only to start a conversation. Every later call uses a conversation-scoped token that expires and is refreshed automatically.
- **Turn on API keys** (`BRIDGE_API_KEYS`) whenever the bridge can be reached by anything you don't fully control. Callers send `Authorization: Bearer <key>` (or `X-API-Key: <key>`). The Agent Card and `/healthz` stay public so clients can discover the agent and probes can check it. The card declares the bearer requirement, so A2A clients know to send it.
- **Terminate TLS** in front of the bridge (a reverse proxy, ingress or cloud load balancer) and set `BRIDGE_PUBLIC_URL` to the `https://` address.
- **Keep end users apart.** Each `contextId` is a separate Copilot Studio conversation with its own anonymous user id. If one bridge serves many end users, give each user their own `contextId`.
- **Copilot Studio end-user sign-in** (topics that require the user to authenticate) is an interactive web-chat flow that an agent-to-agent caller can't complete. Use service-level authentication in your agent's actions instead.

---

## Deploying to production

The bridge runs as a single container. The only state is the in-memory map of conversations, which makes it suitable for:

- **Azure Container Apps / App Service / AKS**, **Google Cloud Run**, **AWS App Runner / ECS**, or any Kubernetes cluster.
- One replica, or several replicas with **session affinity on `contextId`**. Without affinity, a follow-up message could land on a replica that doesn't know the conversation and would start a fresh one.

Checklist:

1. Store the Direct Line secret and API keys in your platform's secret store, not in the image.
2. Set `BRIDGE_PUBLIC_URL` to the public HTTPS URL.
3. Point liveness and readiness probes at `GET /healthz`.
4. Allow SSE through your proxy: disable response buffering and set idle timeouts above `REPLY_TIMEOUT_SECONDS`.
5. Set `DIRECTLINE_BASE_URL` to the region of your Power Platform environment.

Example (Azure Container Apps):

```bash
az containerapp up -n a2a-bridge -g my-rg --source . --ingress external --target-port 8000 \
  --env-vars DIRECTLINE_SECRET=secretref:dl-secret BRIDGE_API_KEYS=secretref:api-keys \
             BRIDGE_PUBLIC_URL=https://a2a-bridge.<region>.azurecontainerapps.io
```

---

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `Configuration error: Set either DIRECTLINE_SECRET or DIRECTLINE_TOKEN_ENDPOINT` | Neither (or both) is set. Check `.env` is in the working directory or pass `--env-file`. |
| Task fails with `Direct Line POST /conversations returned 403` | Wrong or rotated secret, or the token endpoint needs secured access. Copy Secret 1 again. |
| Task fails with `No reply from the Copilot Studio agent within 60s` | The agent isn't published, the topic doesn't send a message, or a slow action is running. Publish the agent and test it in Copilot Studio's test pane, or raise `REPLY_TIMEOUT_SECONDS`. |
| Long answers are cut off | The agent pauses between messages for longer than `IDLE_TIMEOUT_SECONDS`. Raise it to 3–5. |
| The agent forgets earlier messages | The caller isn't reusing `contextId`, the conversation expired (`CONVERSATION_TTL_SECONDS`), or requests hit different replicas without session affinity. |
| Slow responses | Use your regional `DIRECTLINE_BASE_URL`. Generative answers also take a few seconds on the Copilot Studio side. |
| `401 unauthorized` | `BRIDGE_API_KEYS` is set and the caller didn't send `Authorization: Bearer <key>`. |

Set `LOG_LEVEL=DEBUG` to see token refreshes and turn-level details.

---

## Development

```bash
uv sync                    # install runtime + dev dependencies
uv run pytest -q           # unit + end-to-end tests (uses the fake Direct Line, no network)
uv run ruff check .        # lint
uv run ruff format .       # format
```

Project layout:

```
src/a2a_bridge/
  __main__.py        CLI entry point (a2a-bridge)
  app.py             ASGI app: Agent Card, JSON-RPC + REST routes, API-key middleware, /healthz
  executor.py        A2A AgentExecutor: one task = one Copilot Studio turn
  directline.py      Async Direct Line 3.0 client (tokens, send, poll, turn detection)
  conversations.py   contextId -> conversation store (per-context locking, LRU + TTL)
  translate.py       A2A parts <-> Direct Line activities
  config.py          Settings from environment / .env
examples/
  client.py          CLI client using the official A2A SDK
  fake_directline.py Local stand-in for Copilot Studio (used by tests and the quick start)
tests/               pytest suite
```

Contributions are welcome. Please open an issue first for larger changes, and keep `ruff` and `pytest` green.

---

## FAQ

**Do I need to change my Copilot Studio agent?**
No. The bridge uses the standard Direct Line channel that every published Copilot Studio agent already has.

**Which A2A versions are supported?**
A2A 1.0 natively, plus 0.3 clients through the official SDK's compatibility layer, on both JSON-RPC and REST.

**Does it support push notifications?**
Not yet. Turns usually finish in seconds, so use streaming or plain request/response.

**Can I cancel a running task?**
Yes. Cancelling stops the bridge from waiting and marks the task `canceled`. Direct Line can't abort a turn Copilot Studio has already started, but the conversation stays usable.

**Does it work alongside Microsoft Teams or other channels?**
Yes. The bridge is independent of other channels. Your agent can stay published to Teams, web chat and others at the same time.

---

## License

[MIT](LICENSE)
