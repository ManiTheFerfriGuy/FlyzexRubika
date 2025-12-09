from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

LOGGER = logging.getLogger(__name__)


class RubikaAPI:
    """Minimal async client for Rubika Bot API."""

    def __init__(self, token: str, *, session: Optional[httpx.AsyncClient] = None) -> None:
        self.token = token
        self._session = session or httpx.AsyncClient(timeout=20.0)
        self._base_url = f"https://botapi.rubika.ir/v3/{self.token}"
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._session.aclose()

    async def _request(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self._base_url}/{method}"
        async with self._lock:
            response = await self._session.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected response payload for {method}")
        return data

    async def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        inline_keypad: Optional[Dict[str, Any]] = None,
        chat_keypad: Optional[Dict[str, Any]] = None,
        reply_to_message_id: Optional[str] = None,
        disable_notification: bool = False,
    ) -> Optional[str]:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_notification": disable_notification,
        }
        if inline_keypad:
            payload["inline_keypad"] = inline_keypad
        if chat_keypad:
            payload["chat_keypad"] = chat_keypad
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        data = await self._request("sendMessage", payload)
        message_id = data.get("message_id")
        return str(message_id) if message_id is not None else None

    async def edit_message_text(
        self,
        chat_id: str,
        message_id: str,
        text: str,
        *,
        inline_keypad: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if inline_keypad:
            payload["inline_keypad"] = inline_keypad
        await self._request("editMessageText", payload)

    async def get_updates(self, *, offset_id: Optional[str] = None, limit: int = 25) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        payload: Dict[str, Any] = {"limit": limit}
        if offset_id:
            payload["offset_id"] = offset_id
        data = await self._request("getUpdates", payload)
        updates = data.get("updates") or []
        next_offset = data.get("next_offset_id")
        return updates, next_offset

    async def answer_callback_query(self, chat_id: str, message_id: str, text: Optional[str] = None) -> None:
        # Rubika does not require an explicit callback acknowledgement, but we can edit the message to keep parity.
        if text:
            try:
                await self.edit_message_text(chat_id, message_id, text)
            except Exception:
                LOGGER.debug("Failed to echo callback acknowledgement", exc_info=True)

    async def ban_chat_member(self, chat_id: str, user_id: str) -> None:
        LOGGER.warning("ban_chat_member not implemented for Rubika; ignoring request for %s", user_id)

    async def restrict_chat_member(self, chat_id: str, user_id: str, *, permissions: Optional[Dict[str, Any]] = None) -> None:
        LOGGER.warning("restrict_chat_member not implemented for Rubika; ignoring request for %s", user_id)
