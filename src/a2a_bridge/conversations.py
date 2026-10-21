"""Maps A2A context ids to Direct Line conversations."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from a2a_bridge.directline import Conversation, DirectLineClient


@dataclass
class _Session:
    conversation: Conversation | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used: float = field(default_factory=time.monotonic)


class ConversationStore:
    """In-memory, LRU + TTL bounded store of conversations, one per A2A ``contextId``.

    Turns within one context are serialised with a lock, because a Copilot Studio conversation
    can only process one user message at a time. Different contexts run fully in parallel.
    """

    def __init__(self, client: DirectLineClient, *, ttl: float, max_size: int) -> None:
        self._client = client
        self._ttl = ttl
        self._max_size = max_size
        self._sessions: OrderedDict[str, _Session] = OrderedDict()

    def __len__(self) -> int:
        return len(self._sessions)

    @asynccontextmanager
    async def checkout(self, context_id: str) -> AsyncIterator[Conversation]:
        """Hold the context's conversation exclusively for one turn, creating it if needed."""
        session = self._session(context_id)
        async with session.lock:
            conversation = session.conversation
            if conversation is None or conversation.expires_at <= time.monotonic():
                conversation = await self._client.start_conversation()
                session.conversation = conversation
            try:
                yield conversation
            finally:
                session.last_used = time.monotonic()

    def forget(self, context_id: str) -> None:
        """Drop the conversation for a context so the next turn starts a fresh one."""
        session = self._sessions.get(context_id)
        if session is not None:
            session.conversation = None

    def _session(self, context_id: str) -> _Session:
        self._evict()
        session = self._sessions.get(context_id)
        if session is None:
            session = self._sessions[context_id] = _Session()
        self._sessions.move_to_end(context_id)
        return session

    def _evict(self) -> None:
        cutoff = time.monotonic() - self._ttl
        for key in [k for k, s in self._sessions.items() if s.last_used < cutoff]:
            if not self._sessions[key].lock.locked():
                del self._sessions[key]
        while len(self._sessions) >= self._max_size:
            key, session = next(iter(self._sessions.items()))
            if session.lock.locked():
                break
            del self._sessions[key]
