from __future__ import annotations

from types import SimpleNamespace
from typing import Dict, List, Sequence

from ..application_form import ApplicationQuestionDefinition
from ..localization import TextPack, get_default_text_pack

LANGUAGE_CODES: tuple[str, ...] = ("fa", "en")
DEFAULT_LANGUAGE_LABELS: dict[str, str] = {"fa": "فارسی", "en": "English"}


class SimpleButton:
    def __init__(self, text: str, *, callback_data: str | None = None, web_app_url: str | None = None) -> None:
        self.text = text
        self.callback_data = callback_data
        self.web_app = SimpleNamespace(url=web_app_url) if web_app_url else None

    def to_rubika(self) -> Dict[str, str]:
        if self.web_app:
            return {"id": self.callback_data or self.text, "type": "Url", "button_text": self.text, "url": self.web_app.url}
        return {"id": self.callback_data or self.text, "type": "Simple", "button_text": self.text}


class InlineKeyboardMarkup:
    def __init__(self, inline_keyboard: Sequence[Sequence[SimpleButton]]) -> None:
        self.inline_keyboard = [list(row) for row in inline_keyboard]

    def to_rubika(self) -> Dict[str, List[Dict[str, List[Dict[str, str]]]]]:
        return {"rows": [{"buttons": [button.to_rubika() for button in row]} for row in self.inline_keyboard]}


def _button(text: str, callback_data: str) -> SimpleButton:
    return SimpleButton(text, callback_data=callback_data)


def _inline_keyboard(rows: List[List[SimpleButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)


def group_admin_panel_keyboard(
    texts: TextPack | None = None,
    *,
    menu: str = "root",
) -> InlineKeyboardMarkup:
    text_pack = texts or get_default_text_pack()

    if menu == "ban":
        rows = [
            [_button(f"🚫 {text_pack.group_panel_menu_ban_execute_button}", "group_panel:action:ban")],
            [_button(f"ℹ️ {text_pack.group_panel_menu_ban_help_button}", "group_panel:action:ban_help")],
        ]
    elif menu == "mute":
        rows = [
            [_button(f"🔇 {text_pack.group_panel_menu_mute_execute_button}", "group_panel:action:mute")],
            [_button(f"ℹ️ {text_pack.group_panel_menu_mute_help_button}", "group_panel:action:mute_help")],
        ]
    elif menu == "xp":
        rows = [
            [_button(f"📋 {text_pack.group_panel_menu_xp_list_button}", "group_panel:action:xp_members")],
            [
                _button(f"✨ {text_pack.group_panel_menu_xp_add_button}", "group_panel:action:add_xp"),
                _button(f"➖ {text_pack.group_panel_menu_xp_remove_button}", "group_panel:action:remove_xp"),
            ],
        ]
    elif menu == "cups":
        rows = [
            [_button(f"🏆 {text_pack.group_panel_menu_cups_latest_button}", "group_panel:action:cups_latest")],
            [_button(f"ℹ️ {text_pack.group_panel_menu_cups_howto_button}", "group_panel:action:cups_help")],
        ]
    elif menu == "admins":
        rows = [
            [_button(f"🛡️ {text_pack.group_panel_menu_admins_list_button}", "group_panel:action:admins_list")],
            [_button(f"ℹ️ {text_pack.group_panel_menu_admins_howto_button}", "group_panel:action:admins_help")],
        ]
    elif menu == "settings":
        rows = [
            [_button(f"🌐 {text_pack.group_panel_menu_settings_tools_button}", "group_panel:action:settings_tools")],
            [_button(f"ℹ️ {text_pack.group_panel_menu_settings_help_button}", "group_panel:action:settings_help")],
        ]
    else:
        rows = [
            [
                _button(f"ℹ️ {text_pack.group_panel_help_button}", "group_panel:help"),
                _button(f"🔄 {text_pack.group_panel_refresh_button}", "group_panel:refresh"),
            ],
            [
                _button(f"🚫 {text_pack.group_panel_ban_button}", "group_panel:menu:ban"),
                _button(f"🔇 {text_pack.group_panel_mute_button}", "group_panel:menu:mute"),
            ],
            [
                _button(f"✨ {text_pack.group_panel_add_xp_button}", "group_panel:menu:xp"),
                _button(f"🏆 {text_pack.group_panel_manage_cups_button}", "group_panel:menu:cups"),
            ],
            [
                _button(f"🛡️ {text_pack.group_panel_manage_admins_button}", "group_panel:menu:admins"),
                _button(f"⚙️ {text_pack.group_panel_settings_button}", "group_panel:menu:settings"),
            ],
            [_button(f"✖️ {text_pack.group_panel_close_button}", "group_panel:close")],
        ]

    if menu != "root":
        rows.append([_button(f"⬅️ {text_pack.group_panel_menu_back_button}", "group_panel:menu:root")])

    return _inline_keyboard(rows)


def glass_dm_welcome_keyboard(
    texts: TextPack | None = None,
    webapp_url: str | None = None,
    *,
    is_admin: bool = False,
) -> InlineKeyboardMarkup:
    text_pack = texts or get_default_text_pack()
    rows: List[List[SimpleButton]] = [
        [_button(f"🪟 {text_pack.dm_apply_button}", "apply_for_guild")],
        [_button(f"📨 {text_pack.dm_status_button}", "application_status")],
        [_button(f"❌ {text_pack.dm_withdraw_button}", "application_withdraw")],
    ]
    if is_admin:
        rows.append([_button(f"🛡️ {text_pack.dm_admin_panel_button}", "admin_panel")])
    rows.append([_button(f"🌍 {text_pack.dm_language_button}", "language_menu")])
    if webapp_url:
        rows.append([SimpleButton(f"🧊 {text_pack.dm_open_webapp_button}", callback_data="webapp_open", web_app_url=webapp_url)])
    return _inline_keyboard(rows)


def application_review_keyboard(application_id: int, texts: TextPack) -> InlineKeyboardMarkup:
    return _inline_keyboard(
        [
            [
                _button(f"✅ {texts.dm_application_action_buttons.get('approve', 'Approve')}", f"application:{application_id}:approve"),
                _button(f"❌ {texts.dm_application_action_buttons.get('reject', 'Reject')}", f"application:{application_id}:reject"),
            ],
            [_button(f"✍️ {texts.dm_application_action_buttons.get('note', 'Note')}", f"application:{application_id}:note")],
            [_button(f"📊 {texts.dm_application_action_buttons.get('status', 'Status')}", f"application:{application_id}:status")],
        ]
    )


def application_options_keyboard(texts: TextPack) -> InlineKeyboardMarkup:
    return _inline_keyboard(
        [
            [_button(f"✅ {texts.dm_application_action_buttons.get('approve', 'Approve')}", "application:approve")],
            [_button(f"❌ {texts.dm_application_action_buttons.get('reject', 'Reject')}", "application:reject")],
            [_button(f"✍️ {texts.dm_application_action_buttons.get('note', 'Note')}", "application:note")],
        ]
    )


def admin_panel_keyboard(texts: TextPack, webapp_url: str | None = None) -> InlineKeyboardMarkup:
    rows: List[List[SimpleButton]] = [
        [_button(f"⏳ {texts.dm_admin_panel_view_applications_button}", "admin_panel:view_applications")],
        [_button(f"🛡️ {texts.dm_admin_panel_manage_admins_button}", "admin_panel:manage_admins")],
        [_button(f"🧊 {texts.dm_admin_panel_manage_questions_button}", "admin_panel:manage_questions")],
    ]
    if webapp_url:
        rows.append([SimpleButton(f"🌐 {texts.dm_open_webapp_button}", callback_data="admin_panel:webapp", web_app_url=webapp_url)])
    rows.append([_button(f"✖️ {texts.dm_admin_panel_back_button}", "admin_panel:close")])
    return _inline_keyboard(rows)


def admin_management_keyboard(texts: TextPack) -> InlineKeyboardMarkup:
    return _inline_keyboard(
        [
            [_button(f"➕ {texts.dm_admin_manage_add_button}", "admin_panel:promote")],
            [_button(f"➖ {texts.dm_admin_manage_remove_button}", "admin_panel:demote")],
            [_button(f"📝 {texts.dm_admin_manage_list_button}", "admin_panel:list_admins")],
        ]
    )


def admin_questions_keyboard(texts: TextPack, questions=None) -> InlineKeyboardMarkup:
    return _inline_keyboard(
        [
            [_button(f"➕ {texts.dm_admin_questions_add_button}", "admin_panel:question_add")],
            [_button(f"📤 {texts.dm_admin_questions_export_button}", "admin_panel:question_list")],
            [_button(f"♻️ {texts.dm_admin_questions_reset_form_button}", "admin_panel:question_edit")],
        ]
    )


def language_options_keyboard(texts: TextPack) -> InlineKeyboardMarkup:
    return _inline_keyboard(
        [
            [_button(DEFAULT_LANGUAGE_LABELS.get(code, code), f"set_language:{code}") for code in LANGUAGE_CODES],
            [_button(texts.dm_language_close_button, "close_language_menu")],
        ]
    )


def application_review_keyboard_with_language(
    texts: TextPack,
    language_code: str,
    application_id: str,
) -> InlineKeyboardMarkup:
    lang_button = _button(f"🌐 {language_code.upper()}", "language_menu")
    base_rows = application_review_keyboard(application_id, texts).inline_keyboard
    base_rows.append([lang_button])
    return InlineKeyboardMarkup(base_rows)


def show_status_keyboard(texts: TextPack) -> InlineKeyboardMarkup:
    return _inline_keyboard([[_button(texts.dm_application_action_buttons["status"], "application_status")]])


def admin_application_view_keyboard(application_id: str, texts: TextPack) -> InlineKeyboardMarkup:
    return _inline_keyboard(
        [
            [_button(f"✅ {texts.dm_application_action_buttons['approve']}", f"application:{application_id}:approve")],
            [_button(f"❌ {texts.dm_application_action_buttons['reject']}", f"application:{application_id}:reject")],
            [_button(f"✍️ {texts.dm_application_action_buttons['note']}", f"application:{application_id}:note")],
        ]
    )


def leaderboard_refresh_keyboard(prefix: str, chat_id: int | str, texts: TextPack) -> InlineKeyboardMarkup:
    label = getattr(texts, "group_leaderboard_refresh_button", getattr(texts, "group_panel_refresh_button", "Refresh"))
    return _inline_keyboard(
        [[_button(f"🔄 {label}", f"leaderboard:{prefix}:{chat_id}:refresh")]]
    )


def glass_dm_language_keyboard(texts: TextPack) -> InlineKeyboardMarkup:
    return _inline_keyboard(
        [
            [_button(texts.dm_language_fa_button, "set_language:fa")],
            [_button(texts.dm_language_en_button, "set_language:en")],
            [_button(texts.dm_language_close_button, "close_language_menu")],
        ]
    )


def glass_group_panel_keyboard(texts: TextPack) -> InlineKeyboardMarkup:
    return _inline_keyboard(
        [
            [_button(texts.group_panel_my_profile_button, "personal_panel:profile")],
            [_button(texts.group_panel_admin_panel_button, "group_panel:open")],
        ]
    )


def personal_panel_keyboard(texts: TextPack) -> InlineKeyboardMarkup:
    return _inline_keyboard(
        [
            [_button(texts.group_panel_my_profile_button, "personal_panel:profile")],
            [_button(texts.group_panel_my_earnings_button, "personal_panel:earnings")],
            [_button(texts.group_panel_my_cups_button, "personal_panel:cups")],
            [_button(texts.group_panel_back_button, "personal_panel:close")],
        ]
    )


def personal_panel_back_keyboard(texts: TextPack) -> InlineKeyboardMarkup:
    return _inline_keyboard([[_button(texts.group_panel_back_button, "personal_panel:back")]])


def yes_no_keyboard(texts: TextPack) -> InlineKeyboardMarkup:
    return _inline_keyboard(
        [
            [_button(texts.prompt_yes, "yes"), _button(texts.prompt_no, "no")]
        ]
    )


def application_form_keyboard(question: ApplicationQuestionDefinition) -> InlineKeyboardMarkup:
    rows: List[List[SimpleButton]] = []
    for option in question.options:
        rows.append([_button(option.label, option.value)])
    return _inline_keyboard(rows)
