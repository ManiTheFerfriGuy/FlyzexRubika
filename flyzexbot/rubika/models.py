from __future__ import annotations

import logging
from dataclasses import dataclass, field
import asyncio
from typing import Any, Dict, Optional

from .api import RubikaAPI

LOGGER = logging.getLogger(__name__)


@dataclass
class Chat:
    id: str
    type: str = "private"
    api: RubikaAPI | None = None

    async def send_message(self, text: str, *, reply_markup: Optional[Any] = None, reply_to_message_id: Optional[str] = None) -> Optional[str]:
        if not self.api:
            return None
        payload_markup = reply_markup.to_rubika() if hasattr(reply_markup, "to_rubika") else reply_markup
        return await self.api.send_message(
            self.id,
            text,
            inline_keypad=payload_markup,
            reply_to_message_id=reply_to_message_id,
        )

    async def edit_message_text(self, message_id: str, text: str, *, reply_markup: Optional[Dict[str, Any]] = None) -> None:
        if not self.api:
            return
        payload_markup = reply_markup.to_rubika() if hasattr(reply_markup, "to_rubika") else reply_markup
        await self.api.edit_message_text(self.id, message_id, text, inline_keypad=payload_markup)


@dataclass
class User:
    id: str
    username: Optional[str] = None
    full_name: Optional[str] = None
    language_code: Optional[str] = None
    is_bot: bool = False


@dataclass
class Message:
    message_id: str
    chat: Chat
    from_user: Optional[User]
    text: Optional[str] = None
    caption: Optional[str] = None
    reply_to_message: Optional["Message"] = None

    async def reply_text(self, text: str) -> Optional[str]:
        return await self.chat.send_message(text, reply_to_message_id=self.message_id)

    async def reply_html(self, text: str) -> Optional[str]:
        # Rubika does not distinguish formatting here; reuse reply_text
        return await self.reply_text(text)


@dataclass
class CallbackQuery:
    id: str
    from_user: Optional[User]
    data: Optional[str]
    message: Optional[Message]

    async def answer(self, text: Optional[str] = None, show_alert: bool = False) -> None:
        if not self.message:
            return
        if text:
            await self.message.reply_text(text)

    async def edit_message_text(self, text: str, reply_markup: Optional[Dict[str, Any]] = None) -> None:
        if not self.message:
            return
        await self.message.chat.edit_message_text(self.message.message_id, text, reply_markup=reply_markup)


@dataclass
class Update:
    message: Optional[Message] = None
    callback_query: Optional[CallbackQuery] = None
    inline_message: Optional[Message] = None

    @property
    def effective_chat(self) -> Optional[Chat]:
        if self.message:
            return self.message.chat
        if self.callback_query and self.callback_query.message:
            return self.callback_query.message.chat
        if self.inline_message:
            return self.inline_message.chat
        return None

    @property
    def effective_user(self) -> Optional[User]:
        if self.message:
            return self.message.from_user
        if self.callback_query:
            return self.callback_query.from_user
        return None

    @property
    def effective_message(self) -> Optional[Message]:
        return self.message or (self.callback_query.message if self.callback_query else None)


@dataclass
class ApplicationContext:
    bot: RubikaAPI
    bot_data: Dict[str, Any] = field(default_factory=dict)
    user_data: Dict[str, Any] = field(default_factory=dict)
    chat_data: Dict[str, Any] = field(default_factory=dict)

    def create_task(self, coro):
        return asyncio.create_task(coro)


ContextTypes = ApplicationContext
