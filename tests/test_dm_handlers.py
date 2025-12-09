from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from flyzexbot.application_form import (
    ApplicationQuestionDefinition,
    build_default_form,
)
from flyzexbot.handlers.dm import DMHandlers
from flyzexbot.localization import ENGLISH_TEXTS, PERSIAN_TEXTS
from flyzexbot.services.storage import (
    Application,
    ApplicationHistoryEntry,
    LOCAL_TIMEZONE,
    format_timestamp,
)
from flyzexbot.ui.keyboards import admin_panel_keyboard, glass_dm_welcome_keyboard


def build_ts(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> str:
    return format_timestamp(
        datetime(year, month, day, hour, minute, tzinfo=LOCAL_TIMEZONE)
    )


MODERN_TS = build_ts(2024, 6, 1, 12, 0)
NEW_YEAR_TS = build_ts(2024, 1, 1)
MAY_ONE_TS = build_ts(2024, 5, 1, 12, 0)
MAY_TWO_TS = build_ts(2024, 5, 2, 12, 0)


class DummyChat:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def send_message(
        self, text: str, **kwargs: str
    ) -> None:  # noqa: ANN003 - kwargs not used directly
        payload = {"text": text, **kwargs}
        payload.setdefault("parse_mode", "HTML")
        self.messages.append(payload)


class DummyCallbackMessage:
    def __init__(self, chat_id: int = 111, message_id: int = 222) -> None:
        self.chat_id = chat_id
        self.message_id = message_id
        self.edits: list[dict[str, str | None]] = []

    async def edit_text(self, text: str, parse_mode: str | None = None) -> None:
        self.edits.append({"text": text, "parse_mode": parse_mode})


class DummyIncomingMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs: object) -> None:  # noqa: ARG002
        self.replies.append(text)


class DummyCallbackQuery:
    def __init__(
        self, user: DummyUser, chat: DummyChat, data: str | None = None
    ) -> None:
        self.from_user = user
        self.message = SimpleNamespace(chat=chat)
        self.data = data
        self.answer = AsyncMock()
        self.edit_message_text = AsyncMock()


class DummyUser:
    def __init__(self, user_id: int, language_code: str = "fa") -> None:
        self.id = user_id
        self.language_code = language_code
        self.full_name = f"User {user_id}"
        self.username = f"user{user_id}"


class DummyContext:
    def __init__(self, args: list[str], bot: object | None = None) -> None:
        self.args = args
        self.user_data: dict[str, object] = {}
        self.bot = bot or SimpleNamespace(
            edit_message_text=AsyncMock(),
            send_message=AsyncMock(),
        )
        self.bot_data: dict[str, object] = {}
        self.application = None


def _flatten_keyboard(markup) -> list:
    return [button for row in getattr(markup, "inline_keyboard", []) for button in row]


def test_handle_apply_allows_admins_to_start_application() -> None:
    storage = SimpleNamespace(
        is_admin=lambda _: True,
        has_application=lambda _: False,
        get_application_status=lambda _: None,
        get_application_form=lambda language=None: build_default_form(language),
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    user = DummyUser(10)
    query = DummyCallbackQuery(user, chat)
    update = SimpleNamespace(callback_query=query)
    context = DummyContext([])

    asyncio.run(handler.handle_apply_callback(update, context))

    query.answer.assert_awaited()
    query.edit_message_text.assert_awaited_once_with(
        text=PERSIAN_TEXTS.dm_application_started
    )
    assert context.user_data.get("is_filling_application") is True
    flow_state = context.user_data.get("application_flow")
    assert isinstance(flow_state, dict)
    assert flow_state.get("pending_question_id") == "role"
    assert flow_state.get("answered_values") == {}
    assert (
        chat.messages
        and chat.messages[-1]["text"] == PERSIAN_TEXTS.dm_application_role_prompt
    )


def test_handle_apply_prevents_duplicate_for_admin() -> None:
    storage = SimpleNamespace(
        is_admin=lambda _: True,
        has_application=lambda _: True,
        get_application_status=lambda _: None,
        get_application_form=lambda language=None: build_default_form(language),
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    user = DummyUser(11)
    query = DummyCallbackQuery(user, chat)
    update = SimpleNamespace(callback_query=query)
    context = DummyContext([])

    asyncio.run(handler.handle_apply_callback(update, context))

    query.answer.assert_awaited()
    query.edit_message_text.assert_awaited_once_with(
        PERSIAN_TEXTS.dm_application_duplicate
    )
    assert "is_filling_application" not in context.user_data
    assert chat.messages == []


def test_handle_apply_prevents_reapplication_for_members() -> None:
    history_entry = SimpleNamespace(status="approved")
    storage = SimpleNamespace(
        is_admin=lambda _: True,
        has_application=lambda _: False,
        get_application_status=lambda _: history_entry,
        get_application_form=lambda language=None: build_default_form(language),
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    user = DummyUser(12)
    query = DummyCallbackQuery(user, chat)
    update = SimpleNamespace(callback_query=query)
    context = DummyContext([])

    asyncio.run(handler.handle_apply_callback(update, context))

    query.answer.assert_awaited()
    query.edit_message_text.assert_awaited_once_with(
        PERSIAN_TEXTS.dm_application_already_member
    )
    assert "is_filling_application" not in context.user_data
    assert chat.messages == []


def test_multi_step_application_flow_collects_responses() -> None:
    storage = SimpleNamespace(
        has_application=lambda _: False,
        add_application=AsyncMock(return_value=True),
        get_application_status=lambda _: None,
        get_application_form=lambda language=None: build_default_form(language),
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    user = DummyUser(20, language_code="en")
    query = DummyCallbackQuery(user, chat)
    update = SimpleNamespace(callback_query=query)
    context = DummyContext([])

    asyncio.run(handler.handle_apply_callback(update, context))

    flow_state = context.user_data.get("application_flow")
    assert isinstance(flow_state, dict)
    assert flow_state.get("pending_question_id") == "role"
    assert flow_state.get("answers") == []
    assert flow_state.get("answered_values") == {}

    first_message = DummyIncomingMessage("Trader")
    first_update = SimpleNamespace(
        message=first_message,
        effective_user=user,
        effective_chat=chat,
    )

    asyncio.run(handler.receive_application(first_update, context))
    assert (
        first_message.replies[-1]
        == PERSIAN_TEXTS.dm_application_followup_prompts["trader"]
    )

    second_message = DummyIncomingMessage("Market making")
    second_update = SimpleNamespace(
        message=second_message,
        effective_user=user,
        effective_chat=chat,
    )

    asyncio.run(handler.receive_application(second_update, context))
    assert second_message.replies[-1] == PERSIAN_TEXTS.dm_application_goals_prompt

    third_message = DummyIncomingMessage("Build a stronger economy")
    third_update = SimpleNamespace(
        message=third_message,
        effective_user=user,
        effective_chat=chat,
    )

    asyncio.run(handler.receive_application(third_update, context))
    assert third_message.replies[-1] == PERSIAN_TEXTS.dm_application_availability_prompt

    fourth_message = DummyIncomingMessage("Evenings and weekends")
    fourth_update = SimpleNamespace(
        message=fourth_message,
        effective_user=user,
        effective_chat=chat,
    )

    asyncio.run(handler.receive_application(fourth_update, context))

    storage.add_application.assert_awaited_once()
    call_args = storage.add_application.await_args.kwargs
    assert call_args["user_id"] == user.id
    assert call_args["username"] == user.username
    responses = call_args["responses"]
    assert len(responses) == 4
    assert responses[0].question_id == "role"
    assert responses[1].question_id.startswith("followup_")
    summary_reply = fourth_message.replies[-2]
    assert PERSIAN_TEXTS.dm_application_summary_title in summary_reply
    assert "Market making" in summary_reply
    assert "Build a stronger economy" in summary_reply
    assert "Evenings and weekends" in summary_reply
    assert "application_flow" not in context.user_data


def test_application_flow_respects_question_override() -> None:
    override_prompt = "Custom role?"
    custom_form = [
        ApplicationQuestionDefinition(
            question_id="role",
            prompt=override_prompt,
            order=1,
        )
    ]
    storage = SimpleNamespace(
        has_application=lambda _: False,
        add_application=AsyncMock(return_value=True),
        get_application_status=lambda _: None,
        get_application_form=lambda _=None: custom_form,
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    user = DummyUser(25, language_code="en")
    query = DummyCallbackQuery(user, chat)
    update = SimpleNamespace(callback_query=query)
    context = DummyContext([])

    asyncio.run(handler.handle_apply_callback(update, context))

    assert chat.messages
    assert chat.messages[-1]["text"] == override_prompt


def test_glass_dm_welcome_keyboard_includes_webapp_button_when_configured() -> None:
    url = "https://example.com/panel"
    markup = glass_dm_welcome_keyboard(PERSIAN_TEXTS, webapp_url=url)
    buttons = _flatten_keyboard(markup)
    assert any(
        getattr(button, "web_app", None) and button.web_app.url == url
        for button in buttons
    )


def test_glass_dm_welcome_keyboard_includes_admin_button_for_admins() -> None:
    markup = glass_dm_welcome_keyboard(PERSIAN_TEXTS, is_admin=True)
    buttons = _flatten_keyboard(markup)
    assert any(
        getattr(button, "callback_data", "") == "admin_panel" for button in buttons
    )


def test_admin_panel_keyboard_uses_webapp_link_when_available() -> None:
    url = "https://example.com/admin"
    markup = admin_panel_keyboard(PERSIAN_TEXTS, webapp_url=url)
    buttons = _flatten_keyboard(markup)
    assert any(
        getattr(button, "web_app", None) and button.web_app.url == url
        for button in buttons
    )


def test_admin_panel_keyboard_includes_manage_admins_button() -> None:
    markup = admin_panel_keyboard(PERSIAN_TEXTS)
    buttons = _flatten_keyboard(markup)
    assert any(
        getattr(button, "callback_data", "") == "admin_panel:manage_admins"
        for button in buttons
    )


def test_admin_panel_keyboard_includes_manage_questions_button() -> None:
    markup = admin_panel_keyboard(PERSIAN_TEXTS)
    buttons = _flatten_keyboard(markup)
    assert any(
        getattr(button, "callback_data", "") == "admin_panel:manage_questions"
        for button in buttons
    )


def test_manage_questions_sets_pending_state() -> None:
    storage = SimpleNamespace(
        is_admin=lambda _: True,
        get_application_form=lambda _=None: [
            ApplicationQuestionDefinition(
                question_id="goals",
                title="Goals question",
                prompt="Original goals prompt",
                kind="text",
                order=3,
            )
        ],
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    user = DummyUser(30)
    query = DummyCallbackQuery(user, chat, data="admin_panel:manage_questions")
    update = SimpleNamespace(callback_query=query)
    context = DummyContext([])

    asyncio.run(handler.handle_admin_panel_action(update, context))

    query.edit_message_text.assert_awaited()

    query_followup = DummyCallbackQuery(
        user,
        chat,
        data="admin_panel:manage_questions:edit_index:0",
    )
    update_followup = SimpleNamespace(callback_query=query_followup)

    asyncio.run(handler.handle_admin_panel_action(update_followup, context))

    pending = context.user_data.get("pending_question_edit")
    assert isinstance(pending, dict)
    assert pending.get("action") == "edit"
    assert pending.get("question_id") == "goals"
    assert chat.messages
    assert chat.messages[-1].get("parse_mode") == "HTML"
    assert "goals" in chat.messages[-1]["text"]


def test_glass_dm_welcome_keyboard_hides_admin_button_for_regular_users() -> None:
    markup = glass_dm_welcome_keyboard(PERSIAN_TEXTS)
    buttons = _flatten_keyboard(markup)
    assert all(
        getattr(button, "callback_data", "") != "admin_panel" for button in buttons
    )


def test_start_message_includes_webapp_button_metadata() -> None:
    handler = DMHandlers(storage=SimpleNamespace(), owner_id=1)
    chat = DummyChat()
    update = SimpleNamespace(effective_user=DummyUser(1), effective_chat=chat)
    context = DummyContext([])
    url = "https://example.com/panel"
    context.bot_data["webapp_url"] = url

    asyncio.run(handler.start(update, context))

    assert chat.messages
    markup = chat.messages[-1].get("reply_markup")
    buttons = _flatten_keyboard(markup)
    assert any(
        getattr(button, "web_app", None) and button.web_app.url == url
        for button in buttons
    )


def test_start_message_includes_admin_button_for_admin() -> None:
    storage = SimpleNamespace(is_admin=lambda _: True)
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    update = SimpleNamespace(effective_user=DummyUser(2), effective_chat=chat)
    context = DummyContext([])

    asyncio.run(handler.start(update, context))

    assert chat.messages
    buttons = _flatten_keyboard(chat.messages[-1]["reply_markup"])
    assert any(
        getattr(button, "callback_data", "") == "admin_panel" for button in buttons
    )


def test_start_message_hides_admin_button_for_non_admin() -> None:
    storage = SimpleNamespace(is_admin=lambda _: False)
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    update = SimpleNamespace(effective_user=DummyUser(3), effective_chat=chat)
    context = DummyContext([])

    asyncio.run(handler.start(update, context))

    assert chat.messages
    buttons = _flatten_keyboard(chat.messages[-1]["reply_markup"])
    assert all(
        getattr(button, "callback_data", "") != "admin_panel" for button in buttons
    )


def test_list_admins_shows_details() -> None:
    storage = SimpleNamespace(
        list_admins=lambda: [1],
        get_admin_details=lambda: [
            {"user_id": 1, "username": "alpha", "full_name": "Alpha"}
        ],
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    update = SimpleNamespace(effective_user=DummyUser(1), effective_chat=chat)
    context = DummyContext([])

    asyncio.run(handler.list_admins(update, context))

    assert chat.messages
    message = chat.messages[-1]
    assert "Alpha" in message["text"]
    assert "@alpha" in message["text"]


def test_promote_admin_invalid_identifier() -> None:
    storage = SimpleNamespace(add_admin=AsyncMock())
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    update = SimpleNamespace(effective_user=DummyUser(1), effective_chat=chat)
    context = DummyContext(["not-a-number"])

    asyncio.run(handler.promote_admin(update, context))

    storage.add_admin.assert_not_awaited()
    assert (
        chat.messages
        and chat.messages[-1]["text"] == PERSIAN_TEXTS.dm_admin_invalid_user_id
    )


def test_demote_admin_invalid_identifier() -> None:
    storage = SimpleNamespace(remove_admin=AsyncMock())
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    update = SimpleNamespace(effective_user=DummyUser(1), effective_chat=chat)
    context = DummyContext(["invalid"])

    asyncio.run(handler.demote_admin(update, context))

    storage.remove_admin.assert_not_awaited()
    assert (
        chat.messages
        and chat.messages[-1]["text"] == PERSIAN_TEXTS.dm_admin_invalid_user_id
    )


def test_promote_admin_invalid_identifier_english() -> None:
    storage = SimpleNamespace(add_admin=AsyncMock())
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    update = SimpleNamespace(
        effective_user=DummyUser(1, language_code="en"), effective_chat=chat
    )
    context = DummyContext(["oops"])

    asyncio.run(handler.promote_admin(update, context))

    storage.add_admin.assert_not_awaited()
    assert (
        chat.messages
        and chat.messages[-1]["text"] == PERSIAN_TEXTS.dm_admin_invalid_user_id
    )


def test_withdraw_success() -> None:
    storage = SimpleNamespace(withdraw_application=AsyncMock(return_value=True))
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    update = SimpleNamespace(effective_user=DummyUser(5), effective_chat=chat)
    context = DummyContext([])

    asyncio.run(handler.withdraw(update, context))

    storage.withdraw_application.assert_awaited_once_with(5)
    assert (
        chat.messages and chat.messages[-1]["text"] == PERSIAN_TEXTS.dm_withdraw_success
    )


def test_withdraw_not_found() -> None:
    storage = SimpleNamespace(withdraw_application=AsyncMock(return_value=False))
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    update = SimpleNamespace(effective_user=DummyUser(6), effective_chat=chat)
    context = DummyContext([])

    asyncio.run(handler.withdraw(update, context))

    storage.withdraw_application.assert_awaited_once_with(6)
    assert (
        chat.messages
        and chat.messages[-1]["text"] == PERSIAN_TEXTS.dm_withdraw_not_found
    )


def test_list_applications_requires_admin_privileges() -> None:
    storage = SimpleNamespace(
        is_admin=lambda _: False, get_pending_applications=lambda: []
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    update = SimpleNamespace(effective_user=DummyUser(12), effective_chat=chat)
    context = DummyContext([])

    asyncio.run(handler.list_applications(update, context))

    assert chat.messages and chat.messages[-1]["text"] == PERSIAN_TEXTS.dm_admin_only


def test_show_admin_panel_requires_admin_privileges() -> None:
    storage = SimpleNamespace(
        is_admin=lambda _: False, get_pending_applications=lambda: []
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    user = DummyUser(50)
    query = DummyCallbackQuery(user, chat)
    update = SimpleNamespace(callback_query=query)
    context = DummyContext([])

    asyncio.run(handler.show_admin_panel(update, context))

    query.answer.assert_awaited_once_with(PERSIAN_TEXTS.dm_admin_only, show_alert=True)


def test_show_admin_panel_displays_admin_keyboard() -> None:
    storage = SimpleNamespace(
        is_admin=lambda _: True,
        get_pending_applications=lambda: [],
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    user = DummyUser(51)
    query = DummyCallbackQuery(user, chat)
    update = SimpleNamespace(callback_query=query)
    context = DummyContext([])

    asyncio.run(handler.show_admin_panel(update, context))

    query.edit_message_text.assert_awaited()
    kwargs = query.edit_message_text.await_args.kwargs
    assert PERSIAN_TEXTS.dm_admin_panel_intro in kwargs["text"]
    buttons = _flatten_keyboard(kwargs["reply_markup"])
    assert any(
        getattr(button, "callback_data", "") == "admin_panel:view_applications"
        for button in buttons
    )
    assert chat.messages == []


def test_status_without_history() -> None:
    storage = SimpleNamespace(get_application_status=lambda _: None)
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    update = SimpleNamespace(effective_user=DummyUser(7), effective_chat=chat)
    context = DummyContext([])

    asyncio.run(handler.status(update, context))

    assert chat.messages and chat.messages[-1]["text"] == PERSIAN_TEXTS.dm_status_none


def test_status_with_pending_history() -> None:
    history_entry = ApplicationHistoryEntry(
        status="pending", updated_at=NEW_YEAR_TS, note=None
    )
    storage = SimpleNamespace(get_application_status=lambda _: history_entry)
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    update = SimpleNamespace(effective_user=DummyUser(8), effective_chat=chat)
    context = DummyContext([])

    asyncio.run(handler.status(update, context))

    assert chat.messages
    last_message = chat.messages[-1]["text"]
    assert PERSIAN_TEXTS.dm_status_pending in last_message


def test_status_with_approved_history_english() -> None:
    history_entry = ApplicationHistoryEntry(
        status="approved", updated_at=NEW_YEAR_TS, note=None
    )
    storage = SimpleNamespace(get_application_status=lambda _: history_entry)
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    update = SimpleNamespace(
        effective_user=DummyUser(9, language_code="en"), effective_chat=chat
    )
    context = DummyContext([])

    asyncio.run(handler.status(update, context))

    assert chat.messages
    message = chat.messages[-1]["text"]
    assert PERSIAN_TEXTS.dm_status_approved in message
    assert context.user_data.get("preferred_language") == "fa"


def test_handle_admin_panel_action_view_applications() -> None:
    pending_applications = [
        Application(
            user_id=101,
            full_name="Tester",
            username="tester",
            answer="Ready to contribute",
            created_at=MODERN_TS,
            language_code="fa",
        )
    ]
    storage = SimpleNamespace(
        is_admin=lambda _: True,
        get_pending_applications=lambda: pending_applications,
        get_applicants_by_status=lambda status: [],
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    user = DummyUser(51)
    query = DummyCallbackQuery(user, chat, data="admin_panel:view_applications")
    update = SimpleNamespace(callback_query=query)
    context = DummyContext([])

    asyncio.run(handler.handle_admin_panel_action(update, context))

    query.answer.assert_awaited()
    assert chat.messages
    assert any("Tester" in message["text"] for message in chat.messages)


def test_handle_admin_panel_action_view_members() -> None:
    history_entry = ApplicationHistoryEntry(
        status="approved", updated_at=MODERN_TS, note=None
    )
    storage = SimpleNamespace(
        is_admin=lambda _: True,
        get_pending_applications=lambda: [],
        get_applicants_by_status=lambda status: (
            [(321, history_entry)] if status == "approved" else []
        ),
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    user = DummyUser(52)
    query = DummyCallbackQuery(user, chat, data="admin_panel:view_members")
    update = SimpleNamespace(callback_query=query)
    context = DummyContext([])

    asyncio.run(handler.handle_admin_panel_action(update, context))

    query.answer.assert_awaited()
    assert chat.messages
    assert "321" in chat.messages[-1]["text"]


def test_handle_admin_panel_action_manage_admins_shows_panel_for_owner() -> None:
    storage = SimpleNamespace(
        is_admin=lambda _: True,
        get_pending_applications=lambda: [],
        get_applicants_by_status=lambda status: [],
        get_admin_details=lambda: [
            {"user_id": 10, "username": "captain", "full_name": "Captain"}
        ],
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    owner_user = DummyUser(1)
    query = DummyCallbackQuery(owner_user, chat, data="admin_panel:manage_admins")
    update = SimpleNamespace(callback_query=query)
    context = DummyContext([])

    asyncio.run(handler.handle_admin_panel_action(update, context))

    query.edit_message_text.assert_awaited()
    args = query.edit_message_text.await_args.kwargs
    assert PERSIAN_TEXTS.dm_admin_manage_title in args["text"]
    assert "@captain" in args["text"]
    assert args.get("reply_markup") is not None


def test_handle_admin_panel_action_manage_admins_blocks_non_owner() -> None:
    storage = SimpleNamespace(
        is_admin=lambda _: True,
        get_pending_applications=lambda: [],
        get_applicants_by_status=lambda status: [],
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    user = DummyUser(99)
    query = DummyCallbackQuery(user, chat, data="admin_panel:manage_admins")
    update = SimpleNamespace(callback_query=query)
    context = DummyContext([])

    asyncio.run(handler.handle_admin_panel_action(update, context))

    query.answer.assert_awaited_once_with(PERSIAN_TEXTS.dm_not_owner, show_alert=True)
    assert "pending_admin_action" not in context.user_data


def test_handle_admin_panel_action_manage_admins_add_sets_pending_action() -> None:
    storage = SimpleNamespace(
        is_admin=lambda _: True,
        get_pending_applications=lambda: [],
        get_applicants_by_status=lambda status: [],
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    owner_user = DummyUser(1)
    query = DummyCallbackQuery(owner_user, chat, data="admin_panel:manage_admins:add")
    update = SimpleNamespace(callback_query=query)
    context = DummyContext([])

    asyncio.run(handler.handle_admin_panel_action(update, context))

    query.answer.assert_awaited()
    assert context.user_data.get("pending_admin_action") == "promote"
    assert (
        chat.messages
        and chat.messages[-1]["text"] == PERSIAN_TEXTS.dm_admin_panel_add_admin_prompt
    )


def test_handle_admin_panel_action_manage_admins_remove_sets_pending_action() -> None:
    storage = SimpleNamespace(
        is_admin=lambda _: True,
        get_pending_applications=lambda: [],
        get_applicants_by_status=lambda status: [],
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    owner_user = DummyUser(1)
    query = DummyCallbackQuery(
        owner_user, chat, data="admin_panel:manage_admins:remove"
    )
    update = SimpleNamespace(callback_query=query)
    context = DummyContext([])

    asyncio.run(handler.handle_admin_panel_action(update, context))

    query.answer.assert_awaited()
    assert context.user_data.get("pending_admin_action") == "demote"
    assert (
        chat.messages
        and chat.messages[-1]["text"] == PERSIAN_TEXTS.dm_admin_enter_user_id
    )


def test_handle_admin_panel_action_manage_admins_list_sends_details() -> None:
    storage = SimpleNamespace(
        is_admin=lambda _: True,
        get_pending_applications=lambda: [],
        get_applicants_by_status=lambda status: [],
        get_admin_details=lambda: [
            {"user_id": 12, "username": "seer", "full_name": "Seer"}
        ],
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    owner_user = DummyUser(1)
    query = DummyCallbackQuery(owner_user, chat, data="admin_panel:manage_admins:list")
    update = SimpleNamespace(callback_query=query)
    context = DummyContext([])

    asyncio.run(handler.handle_admin_panel_action(update, context))

    query.answer.assert_awaited()
    assert chat.messages
    last_message = chat.messages[-1]
    assert "Seer" in last_message["text"]
    assert "12" in last_message["text"]


def test_handle_admin_panel_action_back_returns_home() -> None:
    storage = SimpleNamespace(
        is_admin=lambda _: True,
        get_pending_applications=lambda: [],
        get_applicants_by_status=lambda status: [],
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    user = DummyUser(1)
    query = DummyCallbackQuery(user, chat, data="admin_panel:back")
    update = SimpleNamespace(callback_query=query)
    context = DummyContext([])

    asyncio.run(handler.handle_admin_panel_action(update, context))

    query.edit_message_text.assert_awaited()
    home_kwargs = query.edit_message_text.await_args.kwargs
    assert PERSIAN_TEXTS.dm_welcome in home_kwargs["text"]


def test_receive_application_promote_admin_flow() -> None:
    storage = SimpleNamespace(
        add_admin=AsyncMock(return_value=True),
        is_admin=lambda _: True,
        get_pending_applications=lambda: [],
        get_applicants_by_status=lambda status: [],
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    message = DummyIncomingMessage("123")
    user = DummyUser(1)
    update = SimpleNamespace(effective_user=user, effective_chat=chat, message=message)
    context = DummyContext([])
    context.user_data["pending_admin_action"] = "promote"

    asyncio.run(handler.receive_application(update, context))

    storage.add_admin.assert_awaited_once_with(123)
    assert context.user_data.get("pending_admin_action") is None
    assert chat.messages and chat.messages[-1][
        "text"
    ] == PERSIAN_TEXTS.dm_admin_added.format(user_id=123)


def test_receive_application_demote_admin_flow() -> None:
    storage = SimpleNamespace(
        remove_admin=AsyncMock(return_value=True),
        is_admin=lambda _: True,
        get_pending_applications=lambda: [],
        get_applicants_by_status=lambda status: [],
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    chat = DummyChat()
    message = DummyIncomingMessage("321")
    user = DummyUser(1)
    update = SimpleNamespace(effective_user=user, effective_chat=chat, message=message)
    context = DummyContext([])
    context.user_data["pending_admin_action"] = "demote"

    asyncio.run(handler.receive_application(update, context))

    storage.remove_admin.assert_awaited_once_with(321)
    assert context.user_data.get("pending_admin_action") is None
    assert chat.messages and chat.messages[-1][
        "text"
    ] == PERSIAN_TEXTS.dm_admin_removed.format(user_id=321)


def test_process_question_edit_response_updates_storage() -> None:
    upsert = AsyncMock()
    delete = AsyncMock()
    storage = SimpleNamespace(
        upsert_application_question_definition=upsert,
        delete_application_question_definition=delete,
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    context = DummyContext([])
    context.user_data["pending_question_edit"] = {
        "action": "edit",
        "question_id": "goals",
        "language_code": "en",
    }

    payload = {
        "question_id": "goals",
        "title": "Goals question",
        "prompt": "Reach new heights",
        "kind": "text",
        "order": 3,
        "required": True,
        "options": [],
        "depends_on": None,
        "depends_value": None,
    }
    message = DummyIncomingMessage(json.dumps(payload))
    update = SimpleNamespace(
        message=message,
        effective_user=DummyUser(50, language_code="en"),
    )

    asyncio.run(handler._process_question_edit_response(update, context))

    assert upsert.await_count == 1
    args, kwargs = upsert.await_args
    definition = args[0]
    assert definition.question_id == "goals"
    assert definition.prompt == "Reach new heights"
    assert kwargs.get("language_code") == "en"
    assert not context.user_data.get("pending_question_edit")
    assert message.replies
    assert (
        PERSIAN_TEXTS.dm_admin_questions_saved.format(label=definition.title)
        in message.replies[-1]
    )


def test_process_question_edit_response_handles_reset_language() -> None:
    reset_form = AsyncMock()
    storage = SimpleNamespace(reset_application_form=reset_form)
    handler = DMHandlers(storage=storage, owner_id=1)
    context = DummyContext([])
    context.user_data["pending_question_edit"] = {
        "action": "reset",
        "language_code": "fa",
    }

    message = DummyIncomingMessage(PERSIAN_TEXTS.dm_admin_questions_reset_keyword)
    update = SimpleNamespace(
        message=message,
        effective_user=DummyUser(60, language_code="fa"),
    )

    asyncio.run(handler._process_question_edit_response(update, context))

    reset_form.assert_awaited_once_with(language_code="fa")
    assert "pending_question_edit" not in context.user_data
    assert message.replies
    assert (
        PERSIAN_TEXTS.dm_admin_questions_reset_language_success in message.replies[-1]
    )


def test_process_question_edit_response_handles_import() -> None:
    import_form = AsyncMock()
    storage = SimpleNamespace(import_application_form=import_form)
    handler = DMHandlers(storage=storage, owner_id=1)
    context = DummyContext([])
    context.user_data["preferred_language"] = "en"
    context.user_data["pending_question_edit"] = {
        "action": "import",
        "language_code": "en",
    }

    payload = [
        {
            "question_id": "role",
            "title": "Role",
            "prompt": "Pick a role",
            "kind": "choice",
            "order": 1,
            "required": True,
            "options": [],
            "depends_on": None,
            "depends_value": None,
        }
    ]
    message = DummyIncomingMessage(json.dumps(payload))
    update = SimpleNamespace(
        message=message,
        effective_user=DummyUser(70, language_code="en"),
    )

    asyncio.run(handler._process_question_edit_response(update, context))

    import_form.assert_awaited_once()
    args, kwargs = import_form.await_args
    definitions = args[0]
    assert definitions[0].question_id == "role"
    assert kwargs.get("language_code") == "en"
    assert message.replies
    assert (
        ENGLISH_TEXTS.dm_admin_questions_import_success.format(count=1)
        in message.replies[-1]
    )


def test_admin_handles_note_for_approval() -> None:
    application = Application(
        user_id=42,
        full_name="Tester",
        username="tester",
        answer="I'd love to help",
        created_at=MAY_ONE_TS,
        language_code="en",
    )
    storage = SimpleNamespace(
        pop_application=AsyncMock(return_value=application),
        mark_application_status=AsyncMock(),
        is_admin=lambda _: True,
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    message = DummyCallbackMessage()
    admin_user = DummyUser(100, language_code="en")
    query = SimpleNamespace(
        data=f"application:{application.user_id}:approve",
        from_user=admin_user,
        message=message,
        answer=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query)
    context = DummyContext([])

    asyncio.run(handler.handle_application_action(update, context))

    assert context.user_data.get("pending_review_note")
    assert message.edits
    prompt_text = message.edits[-1]["text"]
    assert PERSIAN_TEXTS.dm_application_note_skip_keyword in prompt_text

    note_message = DummyIncomingMessage("Welcome to the team!")
    update_note = SimpleNamespace(message=note_message, effective_user=admin_user)

    asyncio.run(handler.receive_application(update_note, context))

    storage.pop_application.assert_awaited_once_with(application.user_id)
    storage.mark_application_status.assert_awaited_once_with(
        application.user_id,
        "approved",
        note="Welcome to the team!",
        language_code=application.language_code,
    )
    send_kwargs = context.bot.send_message.await_args.kwargs
    assert send_kwargs["chat_id"] == application.user_id
    assert "Welcome to the team!" in send_kwargs["text"]
    assert ENGLISH_TEXTS.dm_application_note_label in send_kwargs["text"]
    edit_kwargs = context.bot.edit_message_text.await_args.kwargs
    assert "Welcome to the team!" in edit_kwargs["text"]
    assert PERSIAN_TEXTS.dm_application_note_label in edit_kwargs["text"]
    assert "pending_review_note" not in context.user_data


def test_admin_handles_skip_for_denial() -> None:
    application = Application(
        user_id=77,
        full_name="کاربر",
        username="کاربر77",
        answer="",
        created_at=MAY_TWO_TS,
        language_code="fa",
    )
    storage = SimpleNamespace(
        pop_application=AsyncMock(return_value=application),
        mark_application_status=AsyncMock(),
        is_admin=lambda _: True,
    )
    handler = DMHandlers(storage=storage, owner_id=1)
    message = DummyCallbackMessage()
    admin_user = DummyUser(200, language_code="fa")
    query = SimpleNamespace(
        data=f"application:{application.user_id}:deny",
        from_user=admin_user,
        message=message,
        answer=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query)
    context = DummyContext([])

    asyncio.run(handler.handle_application_action(update, context))

    note_message = DummyIncomingMessage("  صرفنظر  ")
    update_note = SimpleNamespace(message=note_message, effective_user=admin_user)

    asyncio.run(handler.receive_application(update_note, context))

    storage.mark_application_status.assert_awaited_once_with(
        application.user_id,
        "denied",
        note=None,
        language_code=application.language_code,
    )
    send_kwargs = context.bot.send_message.await_args.kwargs
    assert send_kwargs["chat_id"] == application.user_id
    assert PERSIAN_TEXTS.dm_application_note_label not in send_kwargs["text"]
    edit_kwargs = context.bot.edit_message_text.await_args.kwargs
    assert PERSIAN_TEXTS.dm_application_note_label not in edit_kwargs["text"]
