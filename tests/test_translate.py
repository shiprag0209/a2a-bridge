from a2a.helpers import new_data_part, new_text_part
from a2a.types import Message, Role

from a2a_bridge.translate import (
    ADAPTIVE_CARD,
    SUGGESTED_ACTIONS,
    activity_to_parts,
    message_to_activity_input,
)


def _message(*parts):
    return Message(message_id="m", role=Role.ROLE_USER, parts=list(parts))


def test_text_and_data_parts_become_text_and_value():
    text, value = message_to_activity_input(
        _message(new_text_part("hello"), new_text_part("world"), new_data_part({"id": 7}))
    )
    assert text == "hello\nworld"
    assert value == {"id": 7}


def test_multiple_data_parts_become_a_list():
    _, value = message_to_activity_input(_message(new_data_part({"a": 1}), new_data_part([2])))
    assert value == [{"a": 1}, [2]]


def test_empty_message():
    assert message_to_activity_input(None) == ("", None)


def test_markdown_text():
    [part] = activity_to_parts({"type": "message", "text": "**hi**", "textFormat": "markdown"})
    assert part.text == "**hi**"
    assert part.media_type == "text/markdown"


def test_adaptive_card_and_suggested_actions():
    parts = activity_to_parts(
        {
            "type": "message",
            "attachments": [{"contentType": ADAPTIVE_CARD, "content": {"type": "AdaptiveCard"}}],
            "suggestedActions": {"actions": [{"title": "Yes", "value": "yes"}]},
        }
    )
    assert [p.media_type for p in parts] == [ADAPTIVE_CARD, SUGGESTED_ACTIONS]
    assert parts[0].data.struct_value.fields["type"].string_value == "AdaptiveCard"


def test_file_attachment_becomes_url_part():
    [part] = activity_to_parts(
        {
            "type": "message",
            "attachments": [
                {"contentType": "image/png", "contentUrl": "https://x/y.png", "name": "y.png"}
            ],
        }
    )
    assert part.url == "https://x/y.png"
    assert part.filename == "y.png"


def test_empty_activity_has_no_parts():
    assert activity_to_parts({"type": "message", "text": "  "}) == []
