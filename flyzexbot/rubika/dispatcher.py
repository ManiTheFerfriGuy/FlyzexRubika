from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .api import RubikaAPI
from .filters import _Filter, filters
from .models import ApplicationContext, CallbackQuery, Chat, Message, Update, User

LOGGER = logging.getLogger(__name__)


HandlerCallback = Callable[[Update, ApplicationContext], Awaitable[None]]


class BaseHandler:
    def __init__(self, callback: HandlerCallback, *, filters: Optional[_Filter] = None) -> None:
        self.callback = callback
        self.filters = filters

    def matches(self, update: Update) -> bool:
        if self.filters:
            try:
                return self.filters(update)
            except Exception:
                return False
        return True


class CommandHandler(BaseHandler):
    def __init__(self, command: str, callback: HandlerCallback, *, filters: Optional[_Filter] = None) -> None:
        super().__init__(callback, filters=filters)
        self.command = command.lower()

    def matches(self, update: Update) -> bool:
        if not update.effective_message or not update.effective_message.text:
            return False
        text = update.effective_message.text
        if not text.startswith("/"):
            return False
        cmd = text[1:].split()[0].lower()
        if cmd != self.command:
            return False
        return super().matches(update)


class MessageHandler(BaseHandler):
    def matches(self, update: Update) -> bool:
        if not update.effective_message:
            return False
        return super().matches(update)


class CallbackQueryHandler(BaseHandler):
    def __init__(self, callback: HandlerCallback, *, pattern: Optional[str] = None) -> None:
        super().__init__(callback)
        self.pattern = re.compile(pattern) if pattern else None

    def matches(self, update: Update) -> bool:
        if not update.callback_query:
            return False
        if self.pattern and not self.pattern.search(update.callback_query.data or ""):
            return False
        return True


class RubikaApplication:
    def __init__(self, api: RubikaAPI) -> None:
        self.api = api
        self.handlers: List[BaseHandler] = []
        self.bot_data: Dict[str, Any] = {}
        self._user_data: Dict[str, Dict[str, Any]] = {}
        self._chat_data: Dict[str, Dict[str, Any]] = {}
        self._running = False

    def add_handler(self, handler: BaseHandler) -> None:
        self.handlers.append(handler)

    def build_context(self, update: Update) -> ApplicationContext:
        user_id = update.effective_user.id if update.effective_user else ""
        chat_id = update.effective_chat.id if update.effective_chat else ""
        user_data = self._user_data.setdefault(str(user_id), {})
        chat_data = self._chat_data.setdefault(str(chat_id), {})
        return ApplicationContext(bot=self.api, bot_data=self.bot_data, user_data=user_data, chat_data=chat_data)

    async def process_update(self, update: Update) -> None:
        context = self.build_context(update)
        text = update.effective_message.text if update.effective_message else ""
        if text.startswith("/"):
            parts = text.split()
            context.args = parts[1:]
        else:
            context.args = []
        for handler in self.handlers:
            if handler.matches(update):
                try:
                    await handler.callback(update, context)
                except Exception:
                    LOGGER.exception("Handler %s failed", handler)

    async def _poll_updates(self) -> None:
        offset: Optional[str] = None
        while self._running:
            try:
                updates, next_offset = await self.api.get_updates(offset_id=offset)
                for raw in updates:
                    parsed = self._parse_update(raw)
                    if parsed:
                        await self.process_update(parsed)
                offset = next_offset or offset
            except Exception:
                LOGGER.exception("Failed to poll updates")
            await asyncio.sleep(1.0)

    def _parse_update(self, payload: Dict[str, Any]) -> Optional[Update]:
        try:
            if "inline_message" in payload:
                inline = payload["inline_message"]
                chat = Chat(id=str(inline.get("chat_id")), api=self.api)
                message = Message(
                    message_id=str(inline.get("message_id")),
                    chat=chat,
                    from_user=User(id=str(inline.get("sender_id"))),
                    text=inline.get("text"),
                )
                cq = CallbackQuery(
                    id=str(inline.get("message_id")),
                    from_user=message.from_user,
                    data=inline.get("aux_data", {}).get("button_id"),
                    message=message,
                )
                return Update(callback_query=cq)
            update = payload.get("update") or payload
            if "new_message" in update:
                msg_payload = update["new_message"]
                chat_type = "group" if update.get("type", "").lower() == "newmessage" and update.get("chat_id", "").startswith("g") else "private"
                chat = Chat(id=str(update.get("chat_id")), api=self.api, type=chat_type)
                user = User(id=str(msg_payload.get("sender_id")))
                message = Message(
                    message_id=str(msg_payload.get("message_id")),
                    chat=chat,
                    from_user=user,
                    text=msg_payload.get("text"),
                )
                if msg_payload.get("aux_data", {}).get("button_id"):
                    cq = CallbackQuery(
                        id=str(msg_payload.get("message_id")),
                        from_user=user,
                        data=msg_payload.get("aux_data", {}).get("button_id"),
                        message=message,
                    )
                    return Update(callback_query=cq)
                return Update(message=message)
        except Exception:
            LOGGER.exception("Failed to parse update payload: %s", payload)
        return None

    async def run_polling(self) -> None:
        self._running = True
        await self._poll_updates()

    async def stop(self) -> None:
        self._running = False
