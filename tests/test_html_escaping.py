from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"

from flyzexbot.handlers.dm import DMHandlers
from flyzexbot.handlers.group import GroupHandlers
from flyzexbot.services.storage import Application, LOCAL_TIMEZONE, format_timestamp


class DummyChat:
    def __init__(self, chat_id: int = 0) -> None:
        self.id = chat_id
        self.messages: list[dict[str, object]] = []

    async def send_message(self, text: str, reply_markup=None, reply_to_message_id=None) -> None:  # type: ignore[override]
        self.messages.append({
            "text": text,
            "reply_markup": reply_markup,
            "reply_to": reply_to_message_id,
        })


class DMStorageStub:
    def __init__(self, application: Application) -> None:
        self._application = application

    def is_admin(self, user_id: int) -> bool:
        return True

    def get_pending_applications(self) -> list[Application]:
        return [self._application]

    def get_application(self, user_id: int) -> Application | None:
        if user_id == self._application.user_id:
            return self._application
        return None


class GroupStorageStub:
    def get_xp_leaderboard(self, chat_id: int, limit: int) -> list[tuple[str, int]]:
        return [("1", 128)]

    def get_cups(self, chat_id: int, limit: int) -> list[dict[str, object]]:
        return [
            {
                "title": "Champions <Cup>",
                "description": "Best & Bold",
                "podium": ["Alice <A>", "Bob & Co"],
            }
        ]

    def get_group_snapshot(self, chat_id: int) -> dict[str, object]:
        return {}

    def get_user_xp(self, chat_id: int, user_id: int) -> int | None:
        return None


@pytest.mark.anyio("asyncio")
async def test_dm_application_rendering_escapes_html() -> None:
    application = Application(
        user_id=42,
        full_name="Eve <Leader>",
        username="eve<leader>",
        answer="I love & support",
        created_at=format_timestamp(datetime(2024, 1, 1, tzinfo=LOCAL_TIMEZONE)),
    )
    storage = DMStorageStub(application)
    handlers = DMHandlers(storage, owner_id=1)
    chat = DummyChat()
    user = SimpleNamespace(id=1)
    update = SimpleNamespace(effective_chat=chat, effective_user=user)
    context = SimpleNamespace(bot_data={}, user_data={}, chat_data={}, bot=SimpleNamespace(send_message=lambda *_, **__: None))

    await handlers.list_applications(update, context)

    assert chat.messages, "Expected at least one message to be sent"
    text = chat.messages[0]["text"]
    assert "Eve &lt;Leader&gt;" in text
    assert "@eve&lt;leader&gt;" in text
    assert "I love &amp; support" in text


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize(
    "language_code,expected_xp_title,expected_cup_title,expected_separator",
    [
        ("fa", "🏆 جدول امتیاز اعضای فعال", "🥇 جدول افتخارات گیلد", "، "),
        ("en", "🏆 XP board for active members", "🥇 Guild trophy board", ", "),
    ],
)
async def test_group_leaderboards_escape_user_generated_content(
    monkeypatch: pytest.MonkeyPatch,
    language_code: str,
    expected_xp_title: str,
    expected_cup_title: str,
    expected_separator: str,
) -> None:
    storage = GroupStorageStub()
    handlers = GroupHandlers(
        storage,
        xp_per_character=1,
        xp_message_limit=20,
        xp_limit=5,
        cups_limit=5,
    )
    monkeypatch.setattr(
        handlers,
        "_resolve_leaderboard_names",
        AsyncMock(return_value=[("Hero <One>", 256)]),
    )

    chat_xp = DummyChat(chat_id=100)
    user = SimpleNamespace(id=1, language_code=language_code, full_name="Test User")
    update_xp = SimpleNamespace(effective_chat=chat_xp, effective_user=user)
    context = SimpleNamespace(chat_data={}, bot_data={}, user_data={}, bot=SimpleNamespace(send_message=lambda *_, **__: None))

    await handlers.show_xp_leaderboard(update_xp, context)

    assert chat_xp.messages, "XP leaderboard message should be sent"
    xp_text = chat_xp.messages[0]["text"]
    assert xp_text.splitlines()[0] == expected_xp_title
    assert "Hero &lt;One&gt;" in xp_text

    chat_cup = DummyChat(chat_id=100)
    update_cup = SimpleNamespace(effective_chat=chat_cup, effective_user=user)

    await handlers.show_cup_leaderboard(update_cup, context)

    assert chat_cup.messages, "Cup leaderboard message should be sent"
    cup_text = chat_cup.messages[0]["text"]
    assert cup_text.splitlines()[0] == expected_cup_title
    assert "Champions &lt;Cup&gt;" in cup_text
    assert "Best &amp; Bold" in cup_text
    assert "Alice &lt;A&gt;" in cup_text
    assert "Bob &amp; Co" in cup_text
    assert expected_separator in cup_text
