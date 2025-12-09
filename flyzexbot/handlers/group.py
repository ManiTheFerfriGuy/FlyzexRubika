from __future__ import annotations

import asyncio
from html import escape
import logging
import time
from typing import Any, Dict, List, Sequence, Tuple

from ..rubika import (
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    Update,
    filters,
)
from ..rubika.models import ApplicationContext

from ..localization import (AVAILABLE_LANGUAGE_CODES, DEFAULT_LANGUAGE_CODE,
                            PERSIAN_TEXTS, TextPack, get_default_text_pack,
                            get_text_pack, normalize_language_code)
from ..services.analytics import AnalyticsTracker, NullAnalytics
from ..services.storage import Storage
from ..services.xp import calculate_level_progress
from ..ui.keyboards import (group_admin_panel_keyboard,
                            leaderboard_refresh_keyboard)

LOGGER = logging.getLogger(__name__)


class GroupHandlers:
    def __init__(
        self,
        storage: Storage,
        xp_per_character: float,
        xp_message_limit: int,
        xp_limit: int,
        cups_limit: int,
        milestone_interval: int = 5,
        xp_notification_cooldown: int = 180,
        message_cooldown_seconds: float = 20.0,
        analytics: AnalyticsTracker | NullAnalytics | None = None,
    ) -> None:
        self.storage = storage
        self.xp_per_character = max(0.0, xp_per_character)
        self.xp_message_limit = max(0, xp_message_limit)
        self.xp_message_cooldown = max(0.0, message_cooldown_seconds)
        self.xp_limit = xp_limit
        self.cups_limit = cups_limit
        self.milestone_interval = milestone_interval
        self.analytics = analytics or NullAnalytics()
        self.xp_notification_cooldown = max(0, xp_notification_cooldown)
        self._xp_notifications: Dict[Tuple[int, int], float] = {}
        self._message_cooldowns: Dict[Tuple[int, int], float] = {}
        self.personal_panel_cooldown = 30.0

    def build_handlers(self) -> list:
        return [
            MessageHandler(
                filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND,
                self.track_activity,
            ),
            CommandHandler("help", self.command_help, filters=filters.ChatType.GROUPS),
            CommandHandler("myxp", self.command_my_xp, filters=filters.ChatType.GROUPS),
            CommandHandler("xp", self.show_xp_leaderboard, filters=filters.ChatType.GROUPS),
            CommandHandler("cups", self.show_cup_leaderboard, filters=filters.ChatType.GROUPS),
            CommandHandler("add_cup", self.add_cup, filters=filters.ChatType.GROUPS),
            CommandHandler("addxp", self.command_add_xp, filters=filters.ChatType.GROUPS),
            CommandHandler("panel", self.show_panel, filters=filters.ChatType.GROUPS),
            CallbackQueryHandler(self.handle_leaderboard_refresh, pattern=r"^leaderboard:"),
            CallbackQueryHandler(self.handle_panel_action, pattern=r"^group_panel:"),
            CallbackQueryHandler(self.handle_personal_panel_action, pattern=r"^personal_panel:"),
        ]

    async def track_activity(self, update: Update, context: ApplicationContext) -> None:
        message = update.effective_message
        if message is None or update.effective_chat is None or update.effective_user is None:
            return
        if getattr(update.effective_user, "is_bot", False):
            return
        if await self._maybe_handle_panel_response(update, context):
            return
        if message.text and message.text.startswith("/"):
            return
        if self.xp_per_character <= 0 or self.xp_message_limit <= 0:
            return

        content = message.text or message.caption or ""
        char_count = len(content)
        if char_count <= 0:
            return

        xp_amount = int(char_count * self.xp_per_character)
        if xp_amount <= 0:
            return
        if self.xp_message_limit > 0:
            xp_amount = min(xp_amount, self.xp_message_limit)

        key = (update.effective_chat.id, update.effective_user.id)
        if self.xp_message_cooldown:
            now = time.monotonic()
            last_award = self._message_cooldowns.get(key)
            if last_award and now - last_award < self.xp_message_cooldown:
                await self.analytics.record("group.activity_skipped_cooldown")
                return

        texts = self._get_texts(context, getattr(update.effective_user, "language_code", None))
        new_score: int | None = None
        try:
            async with self.analytics.track_time("group.track_activity"):
                new_score = await self.storage.add_xp(
                    chat_id=update.effective_chat.id,
                    user_id=update.effective_user.id,
                    amount=xp_amount,
                    full_name=getattr(update.effective_user, "full_name", None),
                    username=getattr(update.effective_user, "username", None),
                )
        except Exception as exc:
            LOGGER.error("Failed to update XP for %s: %s", update.effective_user.id, exc)
            await self.analytics.record("group.activity_error")
            return
        if new_score is None:
            await self.analytics.record("group.activity_tracked")
            return

        if self.xp_message_cooldown:
            self._message_cooldowns[key] = time.monotonic()

        if self.milestone_interval <= 0:
            await self.analytics.record("group.activity_tracked")
            return

        milestone_base = self.xp_message_limit if self.xp_message_limit > 0 else xp_amount
        milestone_score = milestone_base * self.milestone_interval
        if milestone_score > 0 and new_score % milestone_score == 0:
            should_notify = True
            if self.xp_notification_cooldown:
                last_tick = self._xp_notifications.get(key, 0.0)
                now = time.monotonic()
                if now - last_tick < self.xp_notification_cooldown:
                    should_notify = False
                else:
                    self._xp_notifications[key] = now
            if should_notify:
                await message.reply_text(
                    texts.group_xp_updated.format(
                        full_name=update.effective_user.full_name
                        or update.effective_user.username
                        or str(update.effective_user.id),
                        xp=new_score,
                    )
                )
        await self._maybe_handle_keyword_interaction(
            update,
            context,
            current_total=new_score,
        )
        await self.analytics.record("group.activity_tracked")

    async def command_add_xp(self, update: Update, context: ApplicationContext) -> None:
        chat = update.effective_chat
        actor = update.effective_user
        message = update.effective_message
        if chat is None or actor is None or message is None:
            return
        texts = self._get_texts(context, getattr(actor, "language_code", None))
        if not await self._is_admin(context, chat.id, actor.id):
            await message.reply_text(texts.dm_admin_only)
            return

        target_user = self._resolve_target_from_message(message)
        amount: int | None = None
        if context.args:
            try:
                amount = int(context.args[-1])
            except ValueError:
                amount = None
        if target_user is None and context.args:
            candidate = context.args[0]
            fetched = await self._fetch_member(context, chat.id, candidate)
            if fetched:
                target_user = fetched
        if target_user is None and message.reply_to_message and message.reply_to_message.from_user:
            target_user = message.reply_to_message.from_user
        if target_user is None or amount is None:
            await message.reply_text(texts.group_add_xp_usage)
            return

        try:
            total = await self.storage.add_xp(
                chat.id,
                target_user.id,
                amount,
                full_name=getattr(target_user, "full_name", None),
                username=getattr(target_user, "username", None),
            )
        except Exception as exc:
            LOGGER.error("Failed to grant XP manually: %s", exc)
            await message.reply_text(texts.error_generic)
            return

        await message.reply_text(
            texts.group_add_xp_success.format(
                full_name=target_user.full_name or target_user.username or target_user.id,
                xp=total,
            )
        )

    async def command_help(self, update: Update, context: ApplicationContext) -> None:
        chat = update.effective_chat
        actor = update.effective_user
        message = update.effective_message
        if chat is None or actor is None or message is None:
            return
        texts = self._get_texts(context, getattr(actor, "language_code", None))
        include_admin = False
        try:
            include_admin = await self._is_admin(context, chat.id, actor.id)
        except Exception:
            include_admin = False
        help_text = self._build_help_text(texts, include_admin=include_admin)
        await message.reply_text(help_text)
        await self.analytics.record("group.help_requested")

    async def command_my_xp(self, update: Update, context: ApplicationContext) -> None:
        chat = update.effective_chat
        actor = update.effective_user
        message = update.effective_message
        if chat is None or actor is None or message is None:
            return
        texts = self._get_texts(context, getattr(actor, "language_code", None))
        xp_value = self.storage.get_user_xp(chat.id, actor.id)
        if xp_value is None:
            await message.reply_text(texts.group_myxp_no_data)
            await self.analytics.record("group.my_xp_requested")
            return
        display = escape(
            getattr(actor, "full_name", None)
            or getattr(actor, "username", None)
            or str(actor.id)
        )
        progress = calculate_level_progress(xp_value)
        response = texts.group_myxp_response.format(
            full_name=display,
            xp=xp_value,
            level=progress.level,
            xp_to_next=progress.xp_to_next,
        )
        await message.reply_text(response)
        await self.analytics.record("group.my_xp_requested")

    async def show_panel(self, update: Update, context: ApplicationContext) -> None:
        chat = update.effective_chat
        actor = update.effective_user
        message = update.effective_message
        if chat is None or actor is None or message is None:
            return
        texts = self._get_texts(context, getattr(actor, "language_code", None))
        if not await self._is_admin(context, chat.id, actor.id):
            await message.reply_text(texts.dm_admin_only)
            return

        context.chat_data["group_panel_active_menu"] = "root"
        panel_text, markup = self._compose_group_panel(chat, texts, menu="root")
        await message.reply_text(panel_text, reply_markup=markup)
        await self.analytics.record("group.panel_opened")

    async def handle_panel_action(self, update: Update, context: ApplicationContext) -> None:
        query = update.callback_query
        if not query or not query.data:
            return
        user = query.from_user
        message = query.message
        chat = message.chat if message else None
        if chat is None or user is None:
            return
        if not await self._is_admin(context, chat.id, user.id):
            await query.answer()
            return
        texts = self._get_texts(context, getattr(user, "language_code", None))
        await query.answer()
        parts = query.data.split(":")
        if len(parts) < 2:
            return
        scope = parts[1]
        argument = parts[2] if len(parts) > 2 else None

        if scope == "close":
            if message:
                try:
                    await message.edit_text(texts.group_panel_closed)
                except Exception:
                    await message.reply_text(texts.group_panel_closed)
            context.chat_data.pop("group_panel_active_menu", None)
            return

        if scope == "refresh":
            active_menu = context.chat_data.get("group_panel_active_menu", "root")
            if message:
                panel_text, markup = self._compose_group_panel(
                    chat,
                    texts,
                    menu=str(active_menu),
                )
                try:
                    await message.edit_text(panel_text, reply_markup=markup)
                except Exception:
                    await message.reply_text(panel_text, reply_markup=markup)
            await self.analytics.record("group.panel_refreshed")
            return

        if scope == "help":
            help_text = self._build_help_text(texts, include_admin=True)
            if message:
                await message.reply_text(help_text)
            await self.analytics.record("group.help_requested")
            return

        if scope == "menu":
            target_menu = argument or "root"
            context.chat_data["group_panel_active_menu"] = target_menu
            if message:
                panel_text, markup = self._compose_group_panel(
                    chat,
                    texts,
                    menu=target_menu,
                )
                try:
                    await message.edit_text(panel_text, reply_markup=markup)
                except Exception:
                    await message.reply_text(panel_text, reply_markup=markup)
            return

        if scope != "action":
            return

        action = argument or ""
        if action in {"ban", "mute", "add_xp", "remove_xp"}:
            pending = context.chat_data.setdefault("group_panel_pending", {})
            pending[user.id] = {"action": action}
            prompt_key = {
                "ban": texts.group_panel_ban_prompt,
                "mute": texts.group_panel_mute_prompt,
                "add_xp": texts.group_panel_add_xp_prompt,
                "remove_xp": texts.group_panel_remove_xp_prompt,
            }[action]
            await message.reply_text(prompt_key)
            return

        if action == "ban_help":
            await message.reply_text(texts.group_panel_ban_prompt)
            return

        if action == "mute_help":
            await message.reply_text(texts.group_panel_mute_prompt)
            return

        if action == "xp_members":
            await self._send_xp_members_overview(context, chat.id, message, texts)
            return

        if action == "cups_latest":
            text, markup = self._compose_cup_leaderboard(chat.id, texts)
            await message.reply_text(text, reply_markup=markup)
            return

        if action == "cups_help":
            await message.reply_text(texts.group_panel_cups_hint)
            return

        if action == "admins_list":
            admins_text = self._render_admins_list(texts)
            await message.reply_text(admins_text)
            return

        if action == "admins_help":
            await message.reply_text(texts.group_panel_admins_hint)
            return

        if action in {"settings_tools", "settings_help"}:
            await message.reply_text(texts.group_panel_settings_hint)

    async def show_xp_leaderboard(self, update: Update, context: ApplicationContext) -> None:
        chat = update.effective_chat
        if chat is None:
            return
        texts = self._get_texts(context, getattr(update.effective_user, "language_code", None))
        await self.analytics.record("group.xp_leaderboard_requested")
        text, markup = await self._compose_xp_leaderboard(context, chat.id, texts)
        await chat.send_message(text, reply_markup=markup)

    async def add_cup(self, update: Update, context: ApplicationContext) -> None:
        chat = update.effective_chat
        user = update.effective_user
        if chat is None or user is None:
            return
        texts = self._get_texts(context, getattr(user, "language_code", None))
        if not await self._is_admin(context, chat.id, user.id):
            await chat.send_message(texts.dm_admin_only)
            return
        if not context.args:
            await chat.send_message(texts.group_add_cup_usage)
            return

        raw = " ".join(context.args)
        # Parse "title | description | a,b,c" with basic validation and resilience
        parts = [part.strip() for part in raw.split("|", 2)]
        if len(parts) != 3:
            await chat.send_message(texts.group_add_cup_invalid_format)
            return
        title, description, podium_raw = parts
        if not title or not description:
            await chat.send_message(texts.group_add_cup_invalid_format)
            return
        # Reasonable limits to avoid overly long entries
        if len(title) > 100 or len(description) > 300:
            await chat.send_message(texts.group_add_cup_invalid_format)
            return
        podium = [slot.strip() for slot in podium_raw.split(",") if slot.strip()]
        # Limit podium size and entry length
        if len(podium) > 10 or any(len(entry) > 100 for entry in podium):
            await chat.send_message(texts.group_add_cup_invalid_format)
            return

        try:
            await self.storage.add_cup(chat.id, title, description, podium)
        except Exception as exc:
            LOGGER.error("Failed to add cup in chat %s: %s", chat.id, exc)
            await chat.send_message(texts.group_no_data)
            await self.analytics.record("group.cup_add_error")
            return
        await chat.send_message(texts.group_cup_added.format(title=title))
        await self.analytics.record("group.cup_added")

    async def show_cup_leaderboard(self, update: Update, context: ApplicationContext) -> None:
        chat = update.effective_chat
        if chat is None:
            return
        texts = self._get_texts(context, getattr(update.effective_user, "language_code", None))
        await self.analytics.record("group.cup_leaderboard_requested")
        text, markup = self._compose_cup_leaderboard(chat.id, texts)
        await chat.send_message(text, reply_markup=markup)

    async def handle_leaderboard_refresh(self, update: Update, context: ApplicationContext) -> None:
        query = update.callback_query
        if not query or not query.data:
            return
        await query.answer()
        parts = query.data.split(":", 2)
        if len(parts) != 3:
            return
        _, board_type, chat_id_raw = parts
        message = query.message
        if message is None:
            return
        try:
            chat_id = int(chat_id_raw)
        except ValueError:
            return
        texts = self._get_texts(context, getattr(query.from_user, "language_code", None))
        if board_type == "xp":
            text, markup = await self._compose_xp_leaderboard(context, chat_id, texts)
            await self.analytics.record("group.xp_leaderboard_refreshed")
        else:
            text, markup = self._compose_cup_leaderboard(chat_id, texts)
            await self.analytics.record("group.cup_leaderboard_refreshed")
        await message.chat.edit_message_text(message.message_id, text, reply_markup=markup)

    def _build_help_text(self, texts: TextPack, *, include_admin: bool) -> str:
        lines: List[str] = [texts.group_help_intro, "", texts.group_help_member_title]
        member_commands = [
            ("/help", texts.group_help_cmd_help),
            ("/myxp", texts.group_help_cmd_myxp),
            ("/xp", texts.group_help_cmd_xp),
            ("/cups", texts.group_help_cmd_cups),
        ]
        for command, description in member_commands:
            lines.append(f"<b>{command}</b> — {description}")

        if include_admin:
            lines.extend(["", texts.group_help_admin_title, texts.group_help_admin_hint])
            admin_commands = [
                ("/panel", texts.group_help_cmd_panel),
                ("/add_cup", texts.group_help_cmd_add_cup),
                ("/addxp", texts.group_help_cmd_addxp),
            ]
            for command, description in admin_commands:
                lines.append(f"<b>{command}</b> — {description}")

        lines.extend(["", texts.group_help_footer])
        return "\n".join(lines).strip()

    def _compose_group_panel(
        self,
        chat,
        texts: TextPack,
        *,
        menu: str = "root",
    ) -> Tuple[str, dict]:
        snapshot = self.storage.get_group_snapshot(getattr(chat, "id", 0)) or {}

        chat_title_raw = (
            getattr(chat, "title", None)
            or getattr(chat, "full_name", None)
            or getattr(chat, "username", None)
            or texts.group_panel_unknown_chat
        )
        chat_title = escape(str(chat_title_raw))

        lines: List[str] = [
            texts.group_panel_intro.format(chat_title=chat_title),
            "",
            texts.group_panel_overview_title,
        ]

        metrics: List[str] = [
            texts.group_panel_metric_tracked.format(
                members=int(snapshot.get("members_tracked", 0))
            ),
            texts.group_panel_metric_total_xp.format(
                total_xp=int(snapshot.get("total_xp", 0))
            ),
        ]

        top_member = snapshot.get("top_member")
        if isinstance(top_member, dict) and top_member.get("display"):
            metrics.append(
                texts.group_panel_metric_top_member.format(
                    name=escape(str(top_member.get("display"))),
                    xp=int(top_member.get("xp", 0)),
                    level=int(top_member.get("level", 0)),
                )
            )
        else:
            metrics.append(texts.group_panel_metric_top_member_empty)

        metrics.append(
            texts.group_panel_metric_cups.format(
                count=int(snapshot.get("cup_count", 0))
            )
        )
        metrics.append(
            texts.group_panel_metric_admins.format(
                count=int(snapshot.get("admins_tracked", 0))
            )
        )

        recent_cup = snapshot.get("recent_cup")
        if isinstance(recent_cup, dict) and recent_cup.get("title"):
            metrics.append(
                texts.group_panel_recent_cup.format(
                    title=escape(str(recent_cup.get("title"))),
                    created_at=recent_cup.get("created_at") or "—",
                )
            )

        last_activity = snapshot.get("last_activity")
        if last_activity:
            metrics.append(texts.group_panel_last_activity.format(timestamp=last_activity))

        lines.append("\n".join(metrics))
        lines.append("")
        lines.append(texts.group_panel_actions_hint)

        menu_block = self._build_panel_menu_block(menu, texts)
        if menu_block:
            lines.append("")
            lines.extend(menu_block)

        lines.append("")
        lines.append(texts.group_panel_help_hint)

        text = "\n".join(line for line in lines if line is not None).strip()
        markup = group_admin_panel_keyboard(texts, menu=menu)
        return (text, markup)

    def _build_panel_menu_block(self, menu: str, texts: TextPack) -> List[str]:
        mapping: Dict[str, Tuple[str, str]] = {
            "ban": (
                texts.group_panel_menu_ban_title,
                texts.group_panel_menu_ban_description,
            ),
            "mute": (
                texts.group_panel_menu_mute_title,
                texts.group_panel_menu_mute_description,
            ),
            "xp": (
                texts.group_panel_menu_xp_title,
                texts.group_panel_menu_xp_description,
            ),
            "cups": (
                texts.group_panel_menu_cups_title,
                texts.group_panel_menu_cups_description,
            ),
            "admins": (
                texts.group_panel_menu_admins_title,
                texts.group_panel_menu_admins_description,
            ),
            "settings": (
                texts.group_panel_menu_settings_title,
                texts.group_panel_menu_settings_description,
            ),
        }
        if menu not in mapping:
            return []
        title, description = mapping[menu]
        block: List[str] = [title]
        if description:
            block.append(description)
        return block

    async def _send_xp_members_overview(
        self,
        context: ApplicationContext,
        chat_id: int,
        message,
        texts: TextPack,
    ) -> None:
        limit = 10 if self.xp_limit <= 0 else min(self.xp_limit, 10)
        leaderboard = self.storage.get_xp_leaderboard(chat_id, max(limit, 1))
        if not leaderboard:
            await message.reply_text(texts.group_panel_menu_xp_members_empty)
            return

        resolved = await self._resolve_leaderboard_names(
            context,
            chat_id,
            leaderboard,
        )
        lines: List[str] = []
        for index, (display_name, xp) in enumerate(resolved, start=1):
            safe_name = escape(str(display_name))
            progress = calculate_level_progress(xp)
            lines.append(
                texts.group_panel_menu_xp_members_entry.format(
                    index=index,
                    name=safe_name,
                    xp=xp,
                    level=progress.level,
                )
            )
        members_block = "\n".join(lines)
        text = texts.group_panel_menu_xp_members_header.format(
            count=len(leaderboard),
            members=members_block,
        )
        await message.reply_text(text)

    def _render_admins_list(self, texts: TextPack) -> str:
        details_getter = getattr(self.storage, "get_admin_details", None)
        details: List[Dict[str, Any]] | None = None
        if callable(details_getter):
            details = details_getter()
        else:
            list_getter = getattr(self.storage, "list_admins", None)
            if callable(list_getter):
                details = [{"user_id": admin_id} for admin_id in list_getter()]

        if not details:
            return texts.group_panel_menu_admins_list_empty

        entries: List[str] = []
        for entry in details:
            user_id = entry.get("user_id")
            if user_id is None:
                continue

            parts: List[str] = []
            full_name = entry.get("full_name")
            if full_name:
                parts.append(escape(str(full_name)))

            username = entry.get("username")
            if username:
                normalised = str(username).lstrip("@")
                if normalised:
                    parts.append(f"@{escape(normalised)}")

            if not parts:
                parts.append(texts.group_panel_menu_admins_list_unknown)

            display = " / ".join(parts)
            safe_user_id = escape(str(user_id))
            entries.append(
                texts.group_panel_menu_admins_list_entry.format(
                    display=display,
                    user_id=safe_user_id,
                )
            )

        if not entries:
            return texts.group_panel_menu_admins_list_empty

        admins_block = "\n".join(entries)
        return texts.group_panel_menu_admins_list_header.format(
            count=len(entries),
            admins=admins_block,
        )

    async def _handle_admin_toggle(
        self,
        update: Update,
        context: ApplicationContext,
        *,
        promote: bool,
    ) -> None:
        chat = update.effective_chat
        actor = update.effective_user
        message = update.effective_message
        if chat is None or actor is None or message is None:
            return
        texts = self._get_texts(context, getattr(actor, "language_code", None))
        if not await self._is_admin(context, chat.id, actor.id):
            await message.reply_text(texts.dm_admin_only)
            return

        target_user = self._resolve_target_from_message(message)
        if target_user is None and context.args:
            fetched = await self._fetch_member(context, chat.id, context.args[0])
            if fetched:
                target_user = fetched
        if target_user is None and message.reply_to_message and message.reply_to_message.from_user:
            target_user = message.reply_to_message.from_user
        if target_user is None:
            usage = texts.group_promote_usage if promote else texts.group_demote_usage
            await message.reply_text(usage)
            return

        try:
            if promote:
                changed = await self.storage.add_admin(
                    target_user.id,
                    username=getattr(target_user, "username", None),
                    full_name=getattr(target_user, "full_name", None),
                )
            else:
                changed = await self.storage.remove_admin(target_user.id)
        except Exception as exc:
            LOGGER.error("Failed to toggle admin: %s", exc)
            await message.reply_text(texts.error_generic)
            return

        if promote and not changed:
            await message.reply_text(texts.group_promote_already)
            return
        if not promote and not changed:
            await message.reply_text(texts.group_demote_missing)
            return

        confirmation = texts.group_promote_success if promote else texts.group_demote_success
        await message.reply_text(
            confirmation.format(
                full_name=target_user.full_name or target_user.username or target_user.id
            )
        )

    async def handle_personal_panel_action(
        self, update: Update, context: ApplicationContext
    ) -> None:
        query = update.callback_query
        if not query or not query.data:
            return
        user = query.from_user
        message = query.message
        if user is None or message is None:
            await query.answer()
            return

        parts = query.data.split(":", 3)
        if len(parts) < 3:
            await query.answer()
            return

        _, action, chat_id_raw, *rest = parts
        try:
            chat_id = int(chat_id_raw)
        except ValueError:
            await query.answer()
            return

        texts = self._get_texts(context, getattr(user, "language_code", None))
        await query.answer()

        user_state = self._ensure_personal_panel_state(context)
        chat_title = self._resolve_personal_panel_chat_title(context, chat_id)

        if action == "refresh":
            view = rest[0] if rest else user_state.get("last_view", "profile")
        elif action == "view":
            view = rest[0] if rest else "profile"
            user_state["last_view"] = view
        else:
            return

        try:
            panel_text, markup = await self._compose_personal_panel(
                context,
                chat_id,
                user,
                texts,
                chat_title=chat_title,
                view=view,
            )
        except Exception as exc:
            LOGGER.error("Failed to refresh personal panel: %s", exc)
            return

        user_state["last_view"] = view

        try:
            await message.edit_text(panel_text, reply_markup=markup)
        except Exception:
            await message.reply_text(panel_text, reply_markup=markup)

    async def _maybe_handle_panel_response(
        self, update: Update, context: ApplicationContext
    ) -> bool:
        message = update.effective_message
        chat = update.effective_chat
        actor = update.effective_user
        if message is None or chat is None or actor is None:
            return False
        pending_map = context.chat_data.get("group_panel_pending")
        if not isinstance(pending_map, dict) or actor.id not in pending_map:
            return False

        action = pending_map[actor.id]["action"]
        texts = self._get_texts(context, getattr(actor, "language_code", None))
        if message.text and message.text.lower().strip() == texts.group_panel_cancel_keyword:
            pending_map.pop(actor.id, None)
            await message.reply_text(texts.group_panel_cancelled)
            return True

        if action == "ban":
            target = self._resolve_target_from_message(message)
            if target is None:
                await message.reply_text(texts.group_panel_invalid_target)
                return True
            try:
                await context.bot.ban_chat_member(chat.id, target.id)
            except Exception as exc:
                LOGGER.error("Failed to ban %s: %s", target.id, exc)
                await message.reply_text(texts.group_panel_action_error)
                return True
            pending_map.pop(actor.id, None)
            await message.reply_text(
                texts.group_panel_ban_success.format(
                    full_name=target.full_name or target.username or target.id
                )
            )
            return True

        if action == "mute":
            target = self._resolve_target_from_message(message)
            if target is None:
                await message.reply_text(texts.group_panel_invalid_target)
                return True
            try:
                await context.bot.restrict_chat_member(chat.id, target.id, permissions={"can_send_messages": False})
            except Exception as exc:
                LOGGER.error("Failed to mute %s: %s", target.id, exc)
                await message.reply_text(texts.group_panel_action_error)
                return True
            pending_map.pop(actor.id, None)
            await message.reply_text(
                texts.group_panel_mute_success.format(
                    full_name=target.full_name or target.username or target.id
                )
            )
            return True

        if action in {"add_xp", "remove_xp"}:
            target = self._resolve_target_from_message(message)
            if target is None:
                await message.reply_text(texts.group_panel_invalid_target)
                return True
            try:
                amount = int(message.text.strip())
            except (TypeError, ValueError):
                await message.reply_text(texts.group_add_xp_usage)
                return True
            if amount == 0:
                await message.reply_text(texts.group_add_xp_usage)
                return True
            if action == "add_xp" and amount < 0:
                amount = abs(amount)
            if action == "remove_xp":
                amount = -abs(amount)
            try:
                total = await self.storage.add_xp(
                    chat.id,
                    target.id,
                    amount,
                    full_name=getattr(target, "full_name", None),
                    username=getattr(target, "username", None),
                )
            except Exception as exc:
                LOGGER.error("Failed to grant XP via panel: %s", exc)
                await message.reply_text(texts.error_generic)
                return True
            pending_map.pop(actor.id, None)
            if action == "add_xp":
                template = texts.group_add_xp_success
            else:
                template = texts.group_remove_xp_success
            await message.reply_text(
                template.format(
                    full_name=target.full_name or target.username or target.id,
                    xp=total,
                )
            )
            return True

        return False


    async def _maybe_handle_keyword_interaction(
        self,
        update: Update,
        context: ApplicationContext,
        *,
        current_total: int | None = None,
    ) -> bool:
        message = update.effective_message
        chat = update.effective_chat
        actor = update.effective_user
        if message is None or chat is None or actor is None:
            return False
        if not getattr(message, "text", None):
            return False

        texts = self._get_texts(context, getattr(actor, "language_code", None))
        keyword = self._normalise_keyword(message.text)
        if not keyword:
            return False

        keyword_actions = {
            "xp": "profile",
            "my xp": "profile",
            "current xp": "profile",
            "level": "profile",
            "lvl": "profile",
            "my level": "profile",
            "profile": "profile",
            "my profile": "profile",
            "rank": "profile",
            "my rank": "profile",
            "leaderboard": "leaderboard",
            "trophies": "profile",
            "cups": "profile",
            "moderation panel": "admin_panel",
            "admin panel": "admin_panel",
            "ایکس پی": "profile",
            "ایکس‌پی": "profile",
            "ایکس پی من": "profile",
            "ایکس‌پی من": "profile",
            "سطح": "profile",
            "سطح من": "profile",
            "رتبه": "profile",
            "رتبه من": "profile",
            "نمایه": "profile",
            "نمایه من": "profile",
            "پروفایل": "profile",
            "پروفایل من": "profile",
            "جام": "profile",
            "جام ها": "profile",
            "جام‌ها": "profile",
            "افتخارات": "profile",
            "لیدربورد": "leaderboard",
            "پنل ادمین": "admin_panel",
            "پنل مدیریت": "admin_panel",
            "کنترل پنل": "admin_panel",
        }

        action = keyword_actions.get(keyword)
        if action is None:
            prefixes = (
                "xp",
                "level",
                "rank",
                "profile",
                "leaderboard",
                "trophies",
                "cups",
                "moderation panel",
                "admin panel",
                "ایکس پی",
                "ایکس‌پی",
                "سطح",
                "رتبه",
                "نمایه",
                "پروفایل",
                "جام",
                "لیدربورد",
                "پنل",
            )
            if any(keyword.startswith(prefix) for prefix in prefixes):
                await message.reply_text(texts.group_keyword_fallback)
                await self.analytics.record("group.keyword_fallback")
                return True
            return False

        if action == "admin_panel":
            await self.show_panel(update, context)
            return True

        bot = getattr(context, "bot", None)
        if bot is None or not hasattr(bot, "send_message"):
            return False

        user_state = self._ensure_personal_panel_state(context)
        now = time.monotonic()
        last_sent = float(user_state.get("last_sent", 0.0))
        if now - last_sent < self.personal_panel_cooldown:
            await message.reply_text(texts.group_personal_panel_recently_sent)
            if action == "leaderboard":
                await self.show_xp_leaderboard(update, context)
            return True

        xp_total = current_total
        if xp_total is None:
            xp_total = self.storage.get_user_xp(chat.id, actor.id)

        rank: int | None = None
        total_members = 0
        rank_getter = getattr(self.storage, "get_user_xp_rank", None)
        if callable(rank_getter):
            try:
                rank, total_members = rank_getter(chat.id, actor.id)
            except Exception as exc:
                LOGGER.error("Failed to resolve rank for %s: %s", actor.id, exc)

        trophies = self._collect_user_trophies(chat.id, actor)
        view = "leaderboard" if action == "leaderboard" else user_state.get("last_view", "profile")

        sent = await self._send_personal_panel(
            context,
            chat,
            actor,
            texts,
            view=view,
            current_total=xp_total,
        )
        if not sent:
            await message.reply_text(texts.group_personal_panel_dm_error)
            return True

        user_state["last_sent"] = now
        user_state["last_view"] = view

        display_total = xp_total if xp_total is not None else 0
        progress = calculate_level_progress(display_total)
        rank_display = f"#{rank}" if rank else "—"
        trophies_count = len(trophies)

        if xp_total is None:
            summary = texts.group_personal_panel_dm_prompt_no_data
            summary_kwargs: Dict[str, object] | None = None
        else:
            summary = texts.group_personal_panel_dm_prompt
            summary_kwargs = {
                "xp": display_total,
                "level": progress.level,
                "rank": rank_display,
                "trophies": trophies_count,
            }

        if summary_kwargs:
            await message.reply_text(summary.format(**summary_kwargs))
        else:
            await message.reply_text(summary)

        if action == "leaderboard":
            await self.show_xp_leaderboard(update, context)
        elif keyword in {"trophies", "cups"}:
            if trophies:
                lines = [texts.group_personal_panel_trophies_heading]
                for entry in trophies:
                    lines.append(f"• {entry}")
                await message.reply_text("\n".join(lines))
            else:
                await message.reply_text(texts.group_personal_panel_trophies_empty)

        await self.analytics.record("group.personal_panel_requested")
        return True


    async def _is_admin(self, context: ApplicationContext, chat_id: int, user_id: int) -> bool:
        return self.storage.is_admin(user_id)

    async def _resolve_leaderboard_names(
        self,
        context: ApplicationContext,
        chat_id: int,
        leaderboard: Sequence[Tuple[str, int]],
    ) -> List[Tuple[str, int]]:
        resolved: List[Tuple[str, int]] = []
        for user_id, xp in leaderboard:
            getter = getattr(self.storage, "get_xp_profile", None)
            profile = getter(user_id) if callable(getter) else None
            display = None
            if isinstance(profile, dict):
                display = profile.get("full_name") or profile.get("username")
            if not display:
                display = f"کاربر {user_id}"
            resolved.append((display, xp))
        return resolved

    def _get_texts(
        self,
        context: ApplicationContext,
        language_code: str | None = None,
    ) -> TextPack:
        chat_data = getattr(context, "chat_data", None)
        stored_language: str | None = None
        stored_pack: TextPack | None = None
        if isinstance(chat_data, dict):
            maybe_stored = chat_data.get("preferred_language")
            if isinstance(maybe_stored, str):
                normalised_stored = normalize_language_code(maybe_stored) or maybe_stored
                if normalised_stored in AVAILABLE_LANGUAGE_CODES:
                    stored_language = normalised_stored
                    stored_pack = get_text_pack(stored_language)
                    if normalised_stored != maybe_stored:
                        chat_data["preferred_language"] = normalised_stored

        if stored_pack:
            return stored_pack

        normalised = normalize_language_code(language_code)
        if normalised and normalised in AVAILABLE_LANGUAGE_CODES:
            if isinstance(chat_data, dict) and stored_language is None:
                chat_data["preferred_language"] = normalised
            return get_text_pack(normalised)

        if isinstance(chat_data, dict) and "preferred_language" not in chat_data:
            chat_data["preferred_language"] = DEFAULT_LANGUAGE_CODE
            return get_default_text_pack()

        return get_default_text_pack()

    async def _compose_xp_leaderboard(
        self,
        context: ApplicationContext,
        chat_id: int,
        texts: TextPack,
    ) -> Tuple[str, dict | None]:
        leaderboard = self.storage.get_xp_leaderboard(chat_id, self.xp_limit)
        if not leaderboard:
            return (texts.group_no_data, None)
        resolved = await self._resolve_leaderboard_names(context, chat_id, leaderboard)
        lines: List[str] = [texts.group_xp_leaderboard_title]
        for index, (display_name, xp) in enumerate(resolved, start=1):
            safe_name = escape(str(display_name))
            progress = calculate_level_progress(xp)
            lines.append(
                f"{index}. <b>{safe_name}</b> — <code>{xp}</code> XP · Lv.{progress.level}"
            )
        text = "\n".join(lines)
        markup = leaderboard_refresh_keyboard("xp", chat_id, texts)
        return (text, markup)

    async def _compose_personal_panel(
        self,
        context: ApplicationContext,
        chat_id: int,
        user,
        texts: TextPack,
        *,
        chat_title: str,
        view: str = "profile",
        current_total: int | None = None,
    ) -> Tuple[str, dict]:
        safe_title = escape(str(chat_title))
        xp_total = current_total
        if xp_total is None:
            xp_total = self.storage.get_user_xp(chat_id, getattr(user, "id", 0))
        display_total = xp_total if xp_total is not None else 0
        progress = calculate_level_progress(display_total)
        span = max(1, progress.next_threshold - progress.current_threshold)
        progress_label = texts.group_personal_panel_progress_label.format(
            current=progress.xp_into_level,
            target=span,
        )
        progress_bar = self._render_progress_bar(progress)

        rank: int | None = None
        total_members = 0
        rank_getter = getattr(self.storage, "get_user_xp_rank", None)
        if callable(rank_getter):
            try:
                rank, total_members = rank_getter(chat_id, getattr(user, "id", 0))
            except Exception as exc:
                LOGGER.error("Failed to resolve rank for %s: %s", getattr(user, "id", 0), exc)

        rank_display = f"#{rank}" if rank else "—"

        trophies = self._collect_user_trophies(chat_id, user)

        leaderboard_limit = self.xp_limit if view == "leaderboard" else min(self.xp_limit, 5)
        leaderboard_limit = max(1, leaderboard_limit)
        leaderboard_raw = self.storage.get_xp_leaderboard(chat_id, leaderboard_limit)
        resolved = await self._resolve_leaderboard_names(context, chat_id, leaderboard_raw)

        lines: List[str] = [texts.group_personal_panel_title.format(chat_title=safe_title)]
        lines.append("")
        lines.append(texts.group_personal_panel_profile_heading)
        if xp_total is None:
            lines.append(texts.group_personal_panel_no_data)
        else:
            lines.append(
                texts.group_personal_panel_profile_line.format(
                    xp=display_total,
                    level=progress.level,
                )
            )
            lines.append(
                texts.group_personal_panel_rank_line.format(
                    rank=rank_display,
                    total=max(total_members, 1),
                )
            )
            lines.append(progress_label)
            lines.append(progress_bar)

        lines.append("")
        lines.append(texts.group_personal_panel_trophies_heading)
        if trophies:
            for entry in trophies[:5]:
                lines.append(f"• {escape(str(entry))}")
        else:
            lines.append(texts.group_personal_panel_trophies_empty)

        lines.append("")
        lines.append(texts.group_personal_panel_leaderboard_heading)
        if leaderboard_raw:
            for index, ((candidate_id, xp), (display_name, _)) in enumerate(
                zip(leaderboard_raw, resolved),
                start=1,
            ):
                safe_name = escape(str(display_name))
                member_progress = calculate_level_progress(xp)
                marker = "⭐️ " if str(candidate_id) == str(getattr(user, "id", "")) else ""
                lines.append(
                    texts.group_personal_panel_leaderboard_entry.format(
                        marker=marker,
                        index=index,
                        name=safe_name,
                        xp=xp,
                        level=member_progress.level,
                    )
                )
        else:
            lines.append(texts.group_no_data)

        buttons = [
            [
                {
                    "id": f"personal_panel:view:{chat_id}:profile",
                    "type": "Simple",
                    "button_text": f"👤 {texts.group_personal_panel_profile_button}",
                },
                {
                    "id": f"personal_panel:view:{chat_id}:leaderboard",
                    "type": "Simple",
                    "button_text": f"📊 {texts.group_personal_panel_leaderboard_button}",
                },
            ],
            [
                {
                    "id": f"personal_panel:refresh:{chat_id}:{view}",
                    "type": "Simple",
                    "button_text": f"🔄 {texts.group_personal_panel_refresh_button}",
                }
            ],
        ]

        return ("\n".join(lines), {"rows": [{"buttons": row} for row in buttons]})

    def _compose_cup_leaderboard(
        self,
        chat_id: int,
        texts: TextPack,
    ) -> Tuple[str, dict | None]:
        cups = self.storage.get_cups(chat_id, self.cups_limit)
        if not cups:
            return (texts.group_no_data, None)
        lines: List[str] = [texts.group_cup_leaderboard_title]
        for cup in cups:
            title = escape(str(cup.get("title", "")))
            description = escape(str(cup.get("description", "")))
            podium_entries = [escape(str(slot)) for slot in cup.get("podium", []) if slot]
            separator = "، " if texts is PERSIAN_TEXTS else ", "
            podium = separator.join(podium_entries) if podium_entries else "—"
            lines.append(f"<b>{title}</b> — {description}\n🥇 {podium}")
        text = "\n\n".join(lines)
        markup = leaderboard_refresh_keyboard("cups", chat_id, texts)
        return (text, markup)

    def _ensure_personal_panel_state(self, context: ApplicationContext) -> Dict[str, object]:
        user_data = getattr(context, "user_data", None)
        if not isinstance(user_data, dict):
            setattr(context, "user_data", {})
            user_data = getattr(context, "user_data", {})
        state = user_data.setdefault("personal_panel_state", {})
        if not isinstance(state, dict):
            state = {}
            user_data["personal_panel_state"] = state
        chats = state.get("chats")
        if not isinstance(chats, dict):
            state["chats"] = {}
        return state

    def _resolve_personal_panel_chat_title(
        self, context: ApplicationContext, chat_id: int
    ) -> str:
        state = self._ensure_personal_panel_state(context)
        chats = state.get("chats")
        if isinstance(chats, dict) and chat_id in chats:
            stored = chats.get(chat_id)
            if stored:
                return str(stored)
        return str(chat_id)

    async def _send_personal_panel(
        self,
        context: ApplicationContext,
        chat,
        user,
        texts: TextPack,
        *,
        view: str,
        current_total: int | None,
    ) -> bool:
        bot = getattr(context, "bot", None)
        if bot is None or not hasattr(bot, "send_message"):
            return False

        chat_title_raw = (
            getattr(chat, "title", None)
            or getattr(chat, "full_name", None)
            or getattr(chat, "username", None)
            or texts.group_panel_unknown_chat
        )
        chat_title = str(chat_title_raw)

        state = self._ensure_personal_panel_state(context)
        chats = state.get("chats")
        if isinstance(chats, dict):
            chats[chat.id] = chat_title
        state["last_view"] = view

        try:
            panel_text, markup = await self._compose_personal_panel(
                context,
                chat.id,
                user,
                texts,
                chat_title=chat_title,
                view=view,
                current_total=current_total,
            )
        except Exception as exc:
            LOGGER.error("Failed to compose personal panel: %s", exc)
            return False

        try:
            sent_message = await bot.send_message(
                chat_id=getattr(chat, "id", 0),
                text=panel_text,
                reply_markup=markup,
            )
        except Exception as exc:
            LOGGER.error(
                "Failed to deliver personal panel in chat %s: %s",
                getattr(chat, "id", 0),
                exc,
            )
            return False
        message_id = getattr(sent_message, "message_id", None)
        self._schedule_temporary_message(context, chat.id, message_id)
        return True

    def _schedule_temporary_message(
        self,
        context: ApplicationContext,
        chat_id: int,
        message_id: int | None,
        delay: float = 60.0,
    ) -> None:
        if message_id is None:
            return

        bot = getattr(context, "bot", None)
        if bot is None or not hasattr(bot, "delete_message"):
            return

        async def _delete_later() -> None:
            try:
                await asyncio.sleep(delay)
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception as exc:
                LOGGER.debug(
                    "Failed to delete temporary message %s in chat %s: %s",
                    message_id,
                    chat_id,
                    exc,
                )

        application = getattr(context, "application", None)
        if application and hasattr(application, "create_task"):
            application.create_task(_delete_later())
        else:
            asyncio.create_task(_delete_later())

    def _collect_user_trophies(self, chat_id: int, user) -> List[str]:
        try:
            cups = self.storage.get_cups(chat_id, self.cups_limit)
        except Exception:
            return []
        if not cups:
            return []
        identifiers = {str(getattr(user, "id", "")).casefold()}
        username = getattr(user, "username", None)
        if username:
            identifiers.add(str(username).lstrip("@").casefold())
        full_name = getattr(user, "full_name", None)
        if full_name:
            identifiers.add(str(full_name).casefold())

        trophies: List[str] = []
        for cup in cups:
            title = str(cup.get("title", ""))
            podium = cup.get("podium", []) or []
            for entry in podium:
                entry_str = str(entry).strip()
                normalised = entry_str.lstrip("@").casefold()
                if normalised in identifiers:
                    if title:
                        trophies.append(f"{title} — {entry_str}")
                    else:
                        trophies.append(entry_str)
                    break
        return trophies

    def _render_progress_bar(self, progress, width: int = 10) -> str:
        span = max(1, progress.next_threshold - progress.current_threshold)
        ratio = progress.xp_into_level / span if span else 0.0
        ratio = max(0.0, min(1.0, ratio))
        filled = int(round(ratio * width))
        filled = max(0, min(width, filled))
        return "▰" * filled + "▱" * (width - filled)

    def _normalise_keyword(self, raw_text: str) -> str:
        lowered = raw_text.casefold()
        cleaned = lowered.replace("’", "'")
        condensed = " ".join(cleaned.split())
        return condensed.strip("!?.,:; ")

    def _resolve_target_from_message(self, message) -> object | None:
        reply = getattr(message, "reply_to_message", None)
        if reply and getattr(reply, "from_user", None):
            return reply.from_user
        return None

    async def _fetch_member(
        self,
        context: ApplicationContext,
        chat_id: int,
        raw_identifier: str,
    ) -> object | None:
        candidate = str(raw_identifier).strip()
        if not candidate:
            return None
        try:
            target_id = int(candidate.lstrip("@"))
        except ValueError:
            return None
        try:
            member = await context.bot.get_chat_member(chat_id, target_id)
        except Exception:
            return None
        return getattr(member, "user", None)

