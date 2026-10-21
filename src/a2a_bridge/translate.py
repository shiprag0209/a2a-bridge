"""Translation between A2A messages/parts and Direct Line activities."""

from __future__ import annotations

from typing import Any

from a2a.helpers import get_data_parts, get_text_parts, new_data_part, new_text_part, new_url_part
from a2a.types import Message, Part

ADAPTIVE_CARD = "application/vnd.microsoft.card.adaptive"
SUGGESTED_ACTIONS = "application/vnd.a2a-bridge.suggested-actions+json"


def message_to_activity_input(message: Message | None) -> tuple[str, Any | None]:
    """Return ``(text, value)`` for a Direct Line activity from an incoming A2A message.

    Text parts are joined with newlines. Structured data parts are passed through as the
    activity ``value`` (a single object as-is, several as a list), which Copilot Studio exposes
    to topics as ``Activity.Value``. File parts are not supported by Direct Line text turns
    and are ignored.
    """
    if message is None:
        return "", None
    text = "\n".join(t for t in get_text_parts(message.parts) if t).strip()
    data = get_data_parts(message.parts)
    value: Any | None = None
    if len(data) == 1:
        value = data[0]
    elif data:
        value = data
    return text, value


def activity_to_parts(activity: dict[str, Any]) -> list[Part]:
    """Convert one agent ``message`` activity into A2A parts."""
    parts: list[Part] = []
    text = (activity.get("text") or "").strip()
    if text:
        media_type = "text/markdown" if activity.get("textFormat") == "markdown" else "text/plain"
        parts.append(new_text_part(text, media_type=media_type))

    for attachment in activity.get("attachments") or []:
        part = _attachment_to_part(attachment)
        if part is not None:
            parts.append(part)

    actions = (activity.get("suggestedActions") or {}).get("actions") or []
    choices = [
        {
            "title": a.get("title") or str(a.get("value", "")),
            "value": a.get("value", a.get("title")),
        }
        for a in actions
        if a.get("title") or a.get("value")
    ]
    if choices:
        parts.append(new_data_part({"suggestedActions": choices}, media_type=SUGGESTED_ACTIONS))
    return parts


def _attachment_to_part(attachment: dict[str, Any]) -> Part | None:
    content_type = attachment.get("contentType") or "application/octet-stream"
    content = attachment.get("content")
    url = attachment.get("contentUrl")
    if content is not None:
        if isinstance(content, str):
            return new_text_part(content, media_type=content_type)
        return new_data_part(content, media_type=content_type)
    if url:
        return new_url_part(url, media_type=content_type, filename=attachment.get("name"))
    return None
