# Architecture

This page explains how `a2a-bridge` turns the conversational, channel-based model of Copilot Studio into the task-based model of A2A.

## Components

```
                ┌───────────────────────────── a2a-bridge ─────────────────────────────┐
 A2A client ──▶ │ Starlette app                                                        │
                │  ├─ /.well-known/agent-card.json   (AgentCard built from settings)   │
                │  ├─ /           JSON-RPC  ─┐                                         │
                │  ├─ /rest/...   HTTP+JSON ─┼─▶ DefaultRequestHandler (a2a-sdk)       │
                │  └─ /healthz               │      ├─ InMemoryTaskStore               │
                │                            │      └─ CopilotStudioAgentExecutor ─┐   │
                │  ApiKeyMiddleware (opt.)   │                                     │   │
                │                            │   ConversationStore ◀───────────────┤   │
                │                            │   (contextId → Conversation, locks) │   │
                │                            │                                     ▼   │
                │                            │   DirectLineClient ─────────────────────┼──▶ Direct Line 3.0
                └──────────────────────────────────────────────────────────────────────┘      (Copilot Studio)
```

| Module | Responsibility |
|---|---|
| `app.py` | Builds the Agent Card and mounts the SDK's JSON-RPC, REST and card routes. Adds `/healthz` and optional API-key auth. |
| `executor.py` | Implements the SDK's `AgentExecutor`. One A2A task is one Copilot Studio turn. |
| `conversations.py` | Maps each `contextId` to a Direct Line conversation. Serialises turns per context. Evicts idle conversations (TTL) and caps the total (LRU). |
| `directline.py` | Async Direct Line client: start conversation, refresh token, post activity, poll activities, detect end of turn. |
| `translate.py` | Converts A2A parts to and from Direct Line activities and attachments. |
| `config.py` | Typed, validated settings from the environment or `.env`. |

## Lifecycle of one message

```
Client                    Bridge (executor)                         Direct Line / Copilot Studio
  │ SendMessage(ctx=C) ──▶ │                                               │
  │ ◀── Task (submitted)   │                                               │
  │ ◀── status: working    │                                               │
  │                        │ checkout(C) ─ new? ─▶ POST /conversations ───▶│ (secret or endpoint token)
  │                        │                     ◀── conversationId, token │
  │                        │ POST /activities {text} ─────────────────────▶│
  │                        │ ◀── activity id A                             │
  │                        │ loop: GET /activities?watermark=W ───────────▶│
  │                        │   ◀── typing (replyTo A)   → keep waiting     │
  │ ◀── artifact chunk 1   │   ◀── message (replyTo A)  → stream it        │
  │ ◀── artifact chunk 2   │   ◀── message (replyTo A)  → stream it        │
  │                        │   … quiet for IDLE_TIMEOUT → turn complete    │
  │ ◀── status: completed  │                                               │
```

### Turn detection

Direct Line is a channel protocol, not request/response, so Copilot Studio never says "I'm done". The bridge ends a turn when either:

1. the agent sends an `endOfConversation` activity, or
2. at least one reply has arrived and no further activity (message **or** typing) has arrived for `IDLE_TIMEOUT_SECONDS`.

If nothing has arrived by `REPLY_TIMEOUT_SECONDS`, the task fails with a timeout. If replies arrived but the agent kept typing past the deadline, the turn ends with what was received.

Replies are matched to the turn by `replyToId`. Late replies to an earlier turn are dropped rather than mixed into the current answer.

### Tokens

| Credential | Lifetime | Used for |
|---|---|---|
| Direct Line secret *or* token-endpoint token | long / short | Only `POST /conversations` |
| Conversation token (returned by Direct Line) | ~30 min, refreshed | Every other call for that conversation |

Before each send, the conversation token is refreshed if it expires within two minutes. Conversations whose token has fully expired are started again transparently.

### Concurrency

- Different `contextId`s run fully in parallel.
- Messages in the same `contextId` are queued behind a per-context lock, because a Copilot Studio conversation handles one user message at a time.
- The store is bounded by `MAX_CONVERSATIONS` (LRU) and `CONVERSATION_TTL_SECONDS`. Conversations that are mid-turn are never evicted.

### Failure handling

Every Direct Line error (network, 4xx/5xx, malformed response, timeout) becomes a **failed** task whose status message explains the cause. The conversation for that context is reset, so the next message starts cleanly.

## Scaling out

The task store and conversation map are in memory. To run several replicas, route requests for the same `contextId` to the same replica (sticky sessions). Alternatively, swap `InMemoryTaskStore` for the SDK's database task store and use a single replica per conversation shard.
