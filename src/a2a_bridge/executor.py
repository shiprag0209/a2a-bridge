"""A2A ``AgentExecutor`` that forwards each task to a Copilot Studio agent."""

from __future__ import annotations

import logging
import uuid

from a2a.helpers import new_task, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState

from a2a_bridge.conversations import ConversationStore
from a2a_bridge.directline import DirectLineClient, DirectLineError
from a2a_bridge.translate import activity_to_parts, message_to_activity_input

logger = logging.getLogger(__name__)


class CopilotStudioAgentExecutor(AgentExecutor):
    """Runs one A2A task as one Copilot Studio conversation turn.

    * The A2A ``contextId`` selects the Direct Line conversation, so follow-up messages in the
      same context keep the agent's conversation state (variables, topic flow, history).
    * Every agent message is streamed back as a chunk of a single ``response`` artifact.
    * The task completes when the agent finishes its turn, or fails with a readable reason.
    """

    def __init__(self, client: DirectLineClient, conversations: ConversationStore) -> None:
        self._client = client
        self._conversations = conversations

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or str(uuid.uuid4())
        context_id = context.context_id or str(uuid.uuid4())
        if context.current_task is None:
            history = [context.message] if context.message else []
            await event_queue.enqueue_event(
                new_task(task_id, context_id, TaskState.TASK_STATE_SUBMITTED, history=history)
            )
        updater = TaskUpdater(event_queue, task_id, context_id)

        text, value = message_to_activity_input(context.message)
        if not text and value is None:
            await updater.reject(
                updater.new_agent_message([new_text_part("The message has no text or data.")])
            )
            return

        await updater.start_work()
        artifact_id = str(uuid.uuid4())
        chunks = 0
        try:
            async with self._conversations.checkout(context_id) as conversation:
                async for activity in self._client.converse(conversation, text, value):
                    parts = activity_to_parts(activity)
                    if not parts:
                        continue
                    await updater.add_artifact(
                        parts, artifact_id=artifact_id, name="response", append=chunks > 0
                    )
                    chunks += 1
        except DirectLineError as exc:
            logger.warning("Turn failed for context %s: %s", context_id, exc)
            self._conversations.forget(context_id)
            await updater.failed(updater.new_agent_message([new_text_part(str(exc))]))
            return

        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Direct Line has no way to abort a turn already sent; we stop listening and close the
        # task. The conversation itself stays usable for the next message in this context.
        task_id = context.task_id or (context.current_task.id if context.current_task else "")
        context_id = context.context_id or ""
        await TaskUpdater(event_queue, task_id, context_id).cancel()
