from __future__ import annotations

import logging
from html import escape
import json
from typing import Any, Dict, List, Optional
from ..rubika import (
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    Update,
    filters,
)
from ..rubika.models import ApplicationContext

from ..application_form import (
    ApplicationQuestionDefinition,
    QuestionOption,
    parse_form,
    serialise_form,
)
from ..localization import (
    AVAILABLE_LANGUAGE_CODES,
    DEFAULT_LANGUAGE_CODE,
    TextPack,
    get_default_text_pack,
    get_text_pack,
    normalize_language_code,
)
from ..services.analytics import AnalyticsTracker, NullAnalytics
from ..services.security import RateLimitGuard
from ..services.storage import (
    Application,
    ApplicationHistoryEntry,
    ApplicationResponse,
    Storage,
)
from ..ui.keyboards import (
    admin_management_keyboard,
    admin_panel_keyboard,
    admin_questions_keyboard,
    application_review_keyboard,
    glass_dm_welcome_keyboard,
    language_options_keyboard,
)

LOGGER = logging.getLogger(__name__)


class DMHandlers:
    def __init__(
        self,
        storage: Storage,
        owner_id: int,
        analytics: AnalyticsTracker | NullAnalytics | None = None,
        rate_limiter: RateLimitGuard | None = None,
    ) -> None:
        self.storage = storage
        self.owner_id = owner_id
        self.analytics = analytics or NullAnalytics()
        self.rate_limiter = rate_limiter or RateLimitGuard(10.0, 5)

    def build_handlers(self) -> list:
        private_filter = filters.ChatType.PRIVATE
        return [
            CommandHandler("start", self.start, filters=private_filter),
            CommandHandler("cancel", self.cancel, filters=private_filter),
            MessageHandler(
                self.receive_application,
                filters=private_filter & filters.TEXT & ~filters.COMMAND,
            ),
            CallbackQueryHandler(
                self.handle_apply_callback, pattern="^apply_for_guild$"
            ),
            CallbackQueryHandler(self.show_admin_panel, pattern="^admin_panel$"),
            CallbackQueryHandler(
                self.handle_admin_panel_action, pattern=r"^admin_panel:"
            ),
            CallbackQueryHandler(
                self.show_status_callback, pattern="^application_status$"
            ),
            CallbackQueryHandler(
                self.handle_withdraw_callback, pattern="^application_withdraw$"
            ),
            CallbackQueryHandler(self.show_language_menu, pattern="^language_menu$"),
            CallbackQueryHandler(
                self.close_language_menu, pattern="^close_language_menu$"
            ),
            CallbackQueryHandler(self.set_language_callback, pattern=r"^set_language:"),
            CallbackQueryHandler(
                self.handle_application_action, pattern=r"^application:"
            ),
            CommandHandler("pending", self.list_applications),
            CommandHandler("admins", self.list_admins),
            CommandHandler("promote", self.promote_admin),
            CommandHandler("demote", self.demote_admin),
            CommandHandler("status", self.status),
            CommandHandler("withdraw", self.withdraw),
        ]

    async def start(self, update: Update, context: ApplicationContext) -> None:
        chat = update.effective_chat
        user = update.effective_user
        if chat is None:
            return
        language_code = getattr(user, "language_code", None) if user else None
        texts = self._get_texts(context, language_code)
        is_admin = self._is_admin(user.id) if user else False
        await self.analytics.record("dm.start")
        try:
            await chat.send_message(
                text=self._build_welcome_text(texts),
                reply_markup=glass_dm_welcome_keyboard(
                    texts,
                    self._get_webapp_url(context),
                    is_admin=is_admin,
                ),
            )
        except Exception as exc:
            LOGGER.error("Failed to send welcome message: %s", exc)

    async def handle_apply_callback(
        self, update: Update, context: ApplicationContext
    ) -> None:
        query = update.callback_query
        if not query:
            return

        await query.answer()
        user = query.from_user
        if user is None:
            return

        texts = self._get_texts(context, getattr(user, "language_code", None))
        await self.analytics.record("dm.apply_requested")
        status_entry = self._get_application_status(user.id)
        if (
            status_entry
            and getattr(status_entry, "status", "").casefold() == "approved"
        ):
            await query.edit_message_text(texts.dm_application_already_member)
            return
        if self.storage.has_application(user.id):
            await query.edit_message_text(texts.dm_application_duplicate)
            return

        active_language = self._get_active_language_code(
            context,
            getattr(user, "language_code", None),
        )
        form_definitions = self._get_application_form(active_language)
        next_question = self._select_next_question(form_definitions, {})
        if next_question is None:
            await query.edit_message_text(texts.dm_application_no_questions)
            return

        flow_state = {
            "answers": [],
            "language_code": active_language,
            "form": serialise_form(form_definitions),
            "answered_values": {},
            "pending_question_id": next_question.question_id,
        }
        if isinstance(context.user_data, dict):
            context.user_data["is_filling_application"] = True
            context.user_data["application_flow"] = flow_state
        await query.edit_message_text(
            text=texts.dm_application_started,
        )
        chat = query.message.chat if query.message else None
        if chat is not None:
            await chat.send_message(next_question.prompt)
        return

    async def show_admin_panel(
        self, update: Update, context: ApplicationContext
    ) -> None:
        query = update.callback_query
        if not query:
            return

        user = query.from_user
        message = query.message
        if user is None or message is None:
            return

        texts = self._get_texts(context, getattr(user, "language_code", None))
        if not self._is_admin(user.id):
            await query.answer(texts.dm_admin_only, show_alert=True)
            return

        await query.answer()
        await query.edit_message_text(
            text=self._build_admin_panel_text(texts),
            reply_markup=admin_panel_keyboard(
                texts,
                self._get_webapp_url(context),
            ),
        )
        await self.analytics.record("dm.admin_panel_opened")

    async def handle_admin_panel_action(
        self, update: Update, context: ApplicationContext
    ) -> None:
        query = update.callback_query
        if not query:
            return

        data = query.data or ""
        parts = data.split(":", 1)
        if len(parts) != 2:
            await query.answer()
            return

        _, action = parts
        user = query.from_user
        message = query.message
        if user is None or message is None:
            await query.answer()
            return

        texts = self._get_texts(context, getattr(user, "language_code", None))
        if not self._is_admin(user.id):
            await query.answer(texts.dm_admin_only, show_alert=True)
            return

        chat = message.chat

        if action == "view_applications":
            await query.answer()
            if chat is not None:
                await self._send_pending_applications(chat, texts)
            await self.analytics.record("dm.admin_panel_view_applications")
            return

        if action == "view_members":
            await query.answer()
            if chat is not None:
                members_text = self._render_members_list(
                    self.storage.get_applicants_by_status("approved"),
                    texts,
                )
                await chat.send_message(members_text)
            await self.analytics.record("dm.admin_panel_view_members")
            return

        if action.startswith("manage_admins"):
            if user.id != self.owner_id:
                await query.answer(texts.dm_not_owner, show_alert=True)
                return

            sub_action = ""
            if ":" in action:
                _, sub_action = action.split(":", 1)

            if sub_action == "":
                await query.answer()
                management_text = self._build_admin_management_text(texts)
                await query.edit_message_text(
                    text=management_text,
                    reply_markup=admin_management_keyboard(texts),
                )
                await self.analytics.record("dm.admin_panel_manage_admins_opened")
                return

            if sub_action == "add":
                await query.answer()
                if isinstance(context.user_data, dict):
                    context.user_data["pending_admin_action"] = "promote"
                if chat is not None:
                    await chat.send_message(texts.dm_admin_panel_add_admin_prompt)
                return

            if sub_action == "remove":
                await query.answer()
                if isinstance(context.user_data, dict):
                    context.user_data["pending_admin_action"] = "demote"
                if chat is not None:
                    await chat.send_message(texts.dm_admin_enter_user_id)
                return

            if sub_action == "list":
                await query.answer()
                if chat is not None:
                    admins_text = self._render_admins_list(texts)
                    await chat.send_message(admins_text)
                return

        if action.startswith("manage_questions"):

            await query.answer()
            sub_action = ""
            if ":" in action:
                _, sub_action = action.split(":", 1)

            active_language = self._get_active_language_code(
                context,
                getattr(user, "language_code", None),
            )
            language_label = self._get_language_label(texts, active_language)
            form_definitions = self._get_application_form(active_language)

            if sub_action in ("", "menu"):
                intro = texts.dm_admin_questions_menu_intro.format(
                    reset_keyword=escape(texts.dm_admin_questions_reset_keyword)
                )
                if form_definitions:
                    listing_parts: list[str] = []
                    for definition in form_definitions:
                        display_title = (
                            definition.title
                            or definition.prompt
                            or definition.question_id
                        )
                        listing_parts.append(
                            texts.dm_admin_questions_list_item.format(
                                order=definition.order,
                                title=escape(display_title),
                                question_id=escape(definition.question_id),
                                kind=escape(definition.kind),
                            )
                        )
                    listing = "\n".join(listing_parts)
                else:
                    listing = texts.dm_admin_questions_empty

                menu_text = "\n\n".join(
                    [
                        texts.dm_admin_questions_menu_title.format(
                            language=escape(language_label)
                        ),
                        intro,
                        listing,
                    ]
                )
                await query.edit_message_text(
                    text=menu_text,
                    reply_markup=admin_questions_keyboard(
                        texts, questions=form_definitions
                    ),
                )
                await self.analytics.record("dm.admin_panel_manage_questions_opened")
                return
            if sub_action == "back":
                await query.edit_message_text(
                    text=self._build_admin_panel_text(texts),
                    reply_markup=admin_panel_keyboard(
                        texts,
                        self._get_webapp_url(context),
                    ),
                )
                await self.analytics.record("dm.admin_panel_manage_questions_back")
                return

            if sub_action == "add":
                template_payload = {
                    "question_id": "new_question_id",
                    "title": texts.dm_admin_questions_new_title,
                    "prompt": texts.dm_admin_questions_new_prompt,
                    "kind": "text",
                    "order": len(form_definitions) + 1,
                    "required": True,
                    "options": [],
                    "depends_on": None,
                    "depends_value": None,
                }
                template = json.dumps(template_payload, ensure_ascii=False, indent=2)
                prompt = texts.dm_admin_questions_add_prompt.format(
                    template=escape(template),
                    cancel_keyword=escape(texts.dm_admin_questions_cancel_keyword),
                )
                if chat is not None:
                    await chat.send_message(
                        prompt,
                    )
                if isinstance(context.user_data, dict):
                    context.user_data["pending_question_edit"] = {
                        "action": "add",
                        "language_code": active_language,
                    }
                await self.analytics.record("dm.admin_panel_manage_questions_prompt")
                return

            if sub_action == "import":
                sample = json.dumps(
                    [
                        {
                            "question_id": "sample_question",
                            "title": texts.dm_admin_questions_new_title,
                            "prompt": texts.dm_admin_questions_new_prompt,
                            "kind": "text",
                            "order": 1,
                            "required": True,
                            "options": [],
                            "depends_on": None,
                            "depends_value": None,
                        }
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
                prompt = texts.dm_admin_questions_import_prompt.format(
                    template=escape(sample),
                    cancel_keyword=escape(texts.dm_admin_questions_cancel_keyword),
                )
                if chat is not None:
                    await chat.send_message(
                        prompt,
                    )
                if isinstance(context.user_data, dict):
                    context.user_data["pending_question_edit"] = {
                        "action": "import",
                        "language_code": active_language,
                    }
                await self.analytics.record("dm.admin_panel_manage_questions_prompt")
                return

            if sub_action == "export":
                export_payload = serialise_form(form_definitions)
                export_text = json.dumps(export_payload, ensure_ascii=False, indent=2)
                if chat is not None:
                    await chat.send_message(
                        "\n\n".join(
                            [
                                texts.dm_admin_questions_export_success,
                                f"<pre>{escape(export_text)}</pre>",
                            ]
                        ),
                    )
                return
            if sub_action == "reset":
                prompt = texts.dm_admin_questions_reset_prompt.format(
                    reset_keyword=escape(texts.dm_admin_questions_reset_keyword),
                    cancel_keyword=escape(texts.dm_admin_questions_cancel_keyword),
                )
                if chat is not None:
                    await chat.send_message(
                        prompt,
                    )
                if isinstance(context.user_data, dict):
                    context.user_data["pending_question_edit"] = {
                        "action": "reset",
                        "language_code": active_language,
                    }
                await self.analytics.record("dm.admin_panel_manage_questions_prompt")
                return

            if sub_action.startswith("edit_index:") or sub_action.startswith("edit:"):
                definition: ApplicationQuestionDefinition | None = None
                if sub_action.startswith("edit_index:"):
                    _, index_raw = sub_action.split(":", 1)
                    try:
                        target_index = int(index_raw)
                    except ValueError:
                        target_index = -1
                    if 0 <= target_index < len(form_definitions):
                        definition = form_definitions[target_index]
                else:
                    _, question_id = sub_action.split(":", 1)
                    definition = self._find_question(form_definitions, question_id)
                if definition is None:
                    await message.reply_text(texts.dm_admin_questions_not_found)
                    return
                payload = json.dumps(definition.to_dict(), ensure_ascii=False, indent=2)
                prompt = texts.dm_admin_questions_edit_prompt.format(
                    template=escape(payload),
                    cancel_keyword=escape(texts.dm_admin_questions_cancel_keyword),
                )
                if chat is not None:
                    await chat.send_message(
                        prompt,
                    )
                if isinstance(context.user_data, dict):
                    context.user_data["pending_question_edit"] = {
                        "action": "edit",
                        "language_code": active_language,
                        "question_id": definition.question_id,
                    }
                await self.analytics.record("dm.admin_panel_manage_questions_prompt")
                return

            if sub_action.startswith("delete_index:") or sub_action.startswith(
                "delete:"
            ):
                definition = None
                if sub_action.startswith("delete_index:"):
                    _, index_raw = sub_action.split(":", 1)
                    try:
                        target_index = int(index_raw)
                    except ValueError:
                        target_index = -1
                    if 0 <= target_index < len(form_definitions):
                        definition = form_definitions[target_index]
                else:
                    _, question_id = sub_action.split(":", 1)
                    definition = self._find_question(form_definitions, question_id)
                if definition is None:
                    await message.reply_text(texts.dm_admin_questions_not_found)
                    return
                prompt = texts.dm_admin_questions_delete_prompt.format(
                    title=escape(
                        definition.title or definition.prompt or definition.question_id
                    ),
                    question_id=escape(definition.question_id),
                    confirm_keyword=escape(texts.dm_admin_questions_delete_keyword),
                    cancel_keyword=escape(texts.dm_admin_questions_cancel_keyword),
                )
                if chat is not None:
                    await chat.send_message(
                        prompt,
                    )
                if isinstance(context.user_data, dict):
                    context.user_data["pending_question_edit"] = {
                        "action": "delete",
                        "language_code": active_language,
                        "question_id": definition.question_id,
                    }
                await self.analytics.record("dm.admin_panel_manage_questions_prompt")
                return

            await message.reply_text(texts.dm_admin_questions_not_found)
            return

        if action == "insights":

            await query.answer()
            stats_getter = getattr(self.storage, "get_application_statistics", None)
            if callable(stats_getter) and chat is not None:
                stats = stats_getter()
                insights_text = self._render_admin_insights(stats, texts)
                await chat.send_message(insights_text)
            await self.analytics.record("dm.admin_panel_insights")
            return

        if action == "more_tools":
            await query.answer()
            if chat is not None:
                webapp_url = self._get_webapp_url(context)
                if webapp_url:
                    await chat.send_message(
                        texts.dm_admin_panel_more_tools_text.format(
                            webapp_url=webapp_url
                        ),
                    )
                else:
                    await chat.send_message(texts.dm_admin_panel_more_tools_no_webapp)
            await self.analytics.record("dm.admin_panel_more_tools")
            return

        if action == "back":
            await query.answer()
            await query.edit_message_text(
                text=self._build_welcome_text(texts),
                reply_markup=glass_dm_welcome_keyboard(
                    texts,
                    self._get_webapp_url(context),
                    is_admin=True,
                ),
            )
            await self.analytics.record("dm.admin_panel_back")
            return

        await query.answer()

    async def receive_application(
        self, update: Update, context: ApplicationContext
    ) -> None:
        pending_note = (
            context.user_data.get("pending_review_note")
            if isinstance(context.user_data, dict)
            else None
        )
        if pending_note:
            await self._process_admin_note_response(update, context)
            return

        pending_question = (
            context.user_data.get("pending_question_edit")
            if isinstance(context.user_data, dict)
            else None
        )
        if pending_question:
            await self._process_question_edit_response(update, context)
            return

        if isinstance(context.user_data, dict):
            pending_action = context.user_data.get("pending_admin_action")
            if pending_action == "promote":
                await self._process_admin_promote_response(update, context)
                return
            if pending_action == "demote":
                await self._process_admin_demote_response(update, context)
                return

        if not context.user_data.get("is_filling_application"):
            return

        user = update.effective_user
        if user is None or update.message is None:
            return

        texts = self._get_texts(context, getattr(user, "language_code", None))
        status_entry = self._get_application_status(user.id)
        if (
            status_entry
            and getattr(status_entry, "status", "").casefold() == "approved"
        ):
            await update.message.reply_text(texts.dm_application_already_member)
            context.user_data.pop("is_filling_application", None)
            context.user_data.pop("application_flow", None)
            return
        if not await self.rate_limiter.is_allowed(user.id):
            await self.analytics.record("dm.rate_limited")
            await update.message.reply_text(texts.dm_rate_limited)
            return
        answer = update.message.text.strip()
        if not isinstance(context.user_data, dict):
            return

        flow_state = context.user_data.get("application_flow")
        if isinstance(flow_state, dict):
            language_code = flow_state.get("language_code")
            if language_code is None:
                language_code = self._get_active_language_code(
                    context,
                    getattr(user, "language_code", None),
                )
                flow_state["language_code"] = language_code
            completed = await self._handle_application_flow_step(
                update,
                context,
                texts,
                answer,
                flow_state,
                language_code,
            )
            if completed:
                context.user_data.pop("is_filling_application", None)
                context.user_data.pop("application_flow", None)
            return

        try:
            async with self.analytics.track_time("dm.application_store"):
                success = await self.storage.add_application(
                    user_id=user.id,
                    full_name=user.full_name or user.username or str(user.id),
                    username=getattr(user, "username", None),
                    answer=answer,
                    language_code=context.user_data.get("preferred_language"),
                )
        except Exception as exc:
            LOGGER.error("Failed to persist application for %s: %s", user.id, exc)
            await self.analytics.record("dm.application_error")
            await update.message.reply_text(texts.error_generic)
            context.user_data.pop("is_filling_application", None)
            return
        if not success:
            LOGGER.warning("Duplicate application prevented for user %s", user.id)
            await update.message.reply_text(texts.dm_application_duplicate)
            context.user_data.pop("is_filling_application", None)
            return

        await update.message.reply_text(texts.dm_application_received)
        await self.analytics.record("dm.application_submitted")
        review_chat_id = context.bot_data.get("review_chat_id")
        if review_chat_id:
            await context.bot.send_message(
                chat_id=review_chat_id,
                text=self._render_application_text(user.id),
                reply_markup=application_review_keyboard(
                    user.id, get_default_text_pack()
                ),
            )
        context.user_data.pop("is_filling_application", None)
        return

    async def cancel(self, update: Update, context: ApplicationContext) -> None:
        context.user_data.pop("is_filling_application", None)
        context.user_data.pop("pending_admin_action", None)
        context.user_data.pop("application_flow", None)
        context.user_data.pop("pending_question_edit", None)
        language_code = getattr(update.effective_user, "language_code", None)
        texts = self._get_texts(context, language_code)
        if update.message:
            await update.message.reply_text(texts.dm_cancelled)
        await self.analytics.record("dm.cancelled")

    async def list_applications(
        self, update: Update, context: ApplicationContext
    ) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if user is None or chat is None:
            return
        texts = self._get_texts(context, getattr(user, "language_code", None))
        if not self._is_admin(user.id):
            await chat.send_message(texts.dm_admin_only)
            return
        await self._send_pending_applications(chat, texts)

    async def handle_application_action(
        self, update: Update, context: ApplicationContext
    ) -> None:
        query = update.callback_query
        if not query:
            return
        await query.answer()

        user = query.from_user
        language_code = getattr(user, "language_code", None) if user else None
        admin_texts = self._get_texts(context, language_code)
        if user is None:
            return
        if not self._is_admin(user.id):
            await query.edit_message_text(admin_texts.dm_admin_only)
            return

        data = query.data
        if data == "application:skip":
            await query.edit_message_text(query.message.text if query.message else "")
            await self.analytics.record("dm.admin_skip_application")
            return

        _, user_id_str, action = data.split(":")
        target_id = int(user_id_str)
        application = await self.storage.pop_application(target_id)
        if not application:
            await query.edit_message_text(admin_texts.dm_no_pending)
            return

        applicant_texts = get_text_pack(application.language_code)
        message = query.message
        if message is None:
            return

        application_text = self._format_application_entry(application, admin_texts)
        prompt_template = admin_texts.dm_application_note_prompts.get(action)
        if not prompt_template:
            LOGGER.error("Missing note prompt for action %s", action)
            prompt_template = ""

        prompt_text = prompt_template.format(
            full_name=escape(str(application.full_name)),
            user_id=target_id,
        )
        skip_hint = admin_texts.dm_application_note_skip_hint

        context.user_data["pending_review_note"] = {
            "action": action,
            "target_id": target_id,
            "applicant_texts": applicant_texts,
            "admin_texts": admin_texts,
            "application_text": application_text,
            "chat_id": message.chat_id,
            "message_id": message.message_id,
            "full_name": application.full_name,
            "language_code": application.language_code,
        }

        await message.edit_text(
            text=f"{application_text}\n\n{prompt_text}\n{skip_hint}",
        )

    async def _process_admin_note_response(
        self, update: Update, context: ApplicationContext
    ) -> None:
        message = update.message
        user = update.effective_user
        if message is None or user is None:
            return

        pending_note = context.user_data.get("pending_review_note")
        if not isinstance(pending_note, dict):
            texts = self._get_texts(context, getattr(user, "language_code", None))
            await message.reply_text(texts.dm_application_note_no_active)
            return

        admin_texts: TextPack = pending_note["admin_texts"]
        applicant_texts: TextPack = pending_note["applicant_texts"]
        action: str = pending_note["action"]
        target_id: int = pending_note["target_id"]
        chat_id: int = pending_note["chat_id"]
        message_id: int = pending_note["message_id"]
        application_text: str = pending_note["application_text"]

        note_raw = (message.text or "").strip()
        skip_keyword = admin_texts.dm_application_note_skip_keyword.casefold()
        is_skip = not note_raw or note_raw.casefold() == skip_keyword
        note_to_store = None if is_skip else note_raw

        try:
            status = "approved" if action == "approve" else "denied"
            await self.storage.mark_application_status(
                target_id,
                status,
                note=note_to_store,
                language_code=pending_note.get("language_code"),
            )

            applicant_message = (
                applicant_texts.dm_application_approved_user
                if action == "approve"
                else applicant_texts.dm_application_denied_user
            )
            if note_to_store:
                applicant_message = f"{applicant_message}\n\n📝 {applicant_texts.dm_application_note_label}: {note_to_store}"
            await self._notify_user(context, target_id, applicant_message)

            confirmation_template = admin_texts.dm_application_note_confirmations.get(
                action, ""
            )
            confirmation_text = confirmation_template.format(
                full_name=escape(str(pending_note.get("full_name", target_id))),
                user_id=target_id,
            )
            final_text = f"{application_text}\n\n{confirmation_text}"
            if note_to_store:
                final_text = f"{final_text}\n📝 {admin_texts.dm_application_note_label}: {escape(note_to_store)}"

            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=final_text,
                )
            except Exception as exc:  # pragma: no cover - network failures are logged
                LOGGER.error("Failed to edit admin message for %s: %s", target_id, exc)

            analytics_event = (
                "dm.admin_application_approved"
                if action == "approve"
                else "dm.admin_application_denied"
            )
            await self.analytics.record(analytics_event)
        finally:
            context.user_data.pop("pending_review_note", None)

    async def _process_question_edit_response(
        self,
        update: Update,
        context: ApplicationContext,
    ) -> None:
        message = update.message
        user = update.effective_user
        if message is None or user is None:
            return

        if not isinstance(context.user_data, dict):
            return

        pending = context.user_data.get("pending_question_edit")
        texts = self._get_texts(context, getattr(user, "language_code", None))
        if not isinstance(pending, dict):
            await message.reply_text(texts.dm_admin_questions_cancelled)
            context.user_data.pop("pending_question_edit", None)
            return

        action = str(pending.get("action"))
        language_code = pending.get("language_code")
        payload = (message.text or "").strip()
        cancel_keyword = getattr(
            texts, "dm_admin_questions_cancel_keyword", "/cancel"
        ).casefold()

        if not payload or payload.casefold() == cancel_keyword:
            await message.reply_text(texts.dm_admin_questions_cancelled)
            context.user_data.pop("pending_question_edit", None)
            return

        try:
            if action == "edit":
                await self._handle_question_edit(
                    payload, pending, language_code, texts, message
                )
            elif action == "add":
                await self._handle_question_add(payload, language_code, texts, message)
            elif action == "import":
                await self._handle_question_import(
                    payload, language_code, texts, message
                )
            elif action == "delete":
                await self._handle_question_delete(
                    payload, pending, language_code, texts, message
                )
            elif action == "reset":
                await self._handle_question_reset(
                    payload, language_code, texts, message
                )
            else:
                await message.reply_text(texts.dm_admin_questions_cancelled)
        finally:
            if action in {"edit", "add", "import", "delete", "reset"}:
                context.user_data.pop("pending_question_edit", None)

    async def _handle_question_edit(
        self,
        payload: str,
        pending: Dict[str, Any],
        language_code: str | None,
        texts: TextPack,
        message,
    ) -> None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            await message.reply_text(texts.dm_admin_questions_invalid_payload)
            return

        try:
            definition = ApplicationQuestionDefinition.from_dict(data)
        except Exception:
            await message.reply_text(texts.dm_admin_questions_invalid_payload)
            return

        original_id = str(pending.get("question_id") or "")
        if not definition.question_id:
            await message.reply_text(texts.dm_admin_questions_invalid_payload)
            return

        if original_id and definition.question_id != original_id:
            await self.storage.delete_application_question_definition(
                original_id,
                language_code=language_code,
            )

        await self.storage.upsert_application_question_definition(
            definition,
            language_code=language_code,
        )
        label = definition.title or definition.prompt or definition.question_id
        await message.reply_text(texts.dm_admin_questions_saved.format(label=label))
        await self.analytics.record("dm.admin_panel_manage_questions_saved")

    async def _handle_question_add(
        self,
        payload: str,
        language_code: str | None,
        texts: TextPack,
        message,
    ) -> None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            await message.reply_text(texts.dm_admin_questions_invalid_payload)
            return

        try:
            definition = ApplicationQuestionDefinition.from_dict(data)
        except Exception:
            await message.reply_text(texts.dm_admin_questions_invalid_payload)
            return

        if not definition.question_id:
            await message.reply_text(texts.dm_admin_questions_invalid_payload)
            return

        await self.storage.upsert_application_question_definition(
            definition,
            language_code=language_code,
        )
        label = definition.title or definition.prompt or definition.question_id
        await message.reply_text(texts.dm_admin_questions_saved.format(label=label))
        await self.analytics.record("dm.admin_panel_manage_questions_saved")

    async def _handle_question_import(
        self,
        payload: str,
        language_code: str | None,
        texts: TextPack,
        message,
    ) -> None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            await message.reply_text(texts.dm_admin_questions_invalid_payload)
            return

        if not isinstance(data, list):
            await message.reply_text(texts.dm_admin_questions_invalid_payload)
            return

        definitions = []
        for entry in data:
            if not isinstance(entry, dict):
                await message.reply_text(texts.dm_admin_questions_invalid_payload)
                return
            try:
                definitions.append(ApplicationQuestionDefinition.from_dict(entry))
            except Exception:
                await message.reply_text(texts.dm_admin_questions_invalid_payload)
                return

        await self.storage.import_application_form(
            definitions, language_code=language_code
        )
        await message.reply_text(
            texts.dm_admin_questions_import_success.format(count=len(definitions))
        )
        await self.analytics.record("dm.admin_panel_manage_questions_saved")

    async def _handle_question_delete(
        self,
        payload: str,
        pending: Dict[str, Any],
        language_code: str | None,
        texts: TextPack,
        message,
    ) -> None:
        confirm_keyword = getattr(
            texts, "dm_admin_questions_delete_keyword", "confirm"
        ).casefold()
        if payload.casefold() != confirm_keyword:
            await message.reply_text(texts.dm_admin_questions_cancelled)
            return

        question_id = str(pending.get("question_id") or "")
        if not question_id:
            await message.reply_text(texts.dm_admin_questions_cancelled)
            return

        deleted = await self.storage.delete_application_question_definition(
            question_id,
            language_code=language_code,
        )
        if deleted:
            await message.reply_text(texts.dm_admin_questions_deleted)
            await self.analytics.record("dm.admin_panel_manage_questions_saved")
        else:
            await message.reply_text(texts.dm_admin_questions_cancelled)

    async def _handle_question_reset(
        self,
        payload: str,
        language_code: str | None,
        texts: TextPack,
        message,
    ) -> None:
        reset_keyword = texts.dm_admin_questions_reset_keyword.casefold()
        if payload.casefold() != reset_keyword:
            await message.reply_text(texts.dm_admin_questions_cancelled)
            return

        await self.storage.reset_application_form(language_code=language_code)
        await message.reply_text(texts.dm_admin_questions_reset_language_success)
        await self.analytics.record("dm.admin_panel_manage_questions_saved")

    async def _process_admin_promote_response(
        self, update: Update, context: ApplicationContext
    ) -> None:
        message = update.message
        user = update.effective_user
        chat = update.effective_chat
        if message is None or user is None or chat is None:
            return

        if user.id != self.owner_id:
            context.user_data.pop("pending_admin_action", None)
            return

        texts = self._get_texts(context, getattr(user, "language_code", None))
        payload = (message.text or "").strip()
        try:
            target_user_id = int(payload)
        except (TypeError, ValueError):
            await message.reply_text(texts.dm_admin_invalid_user_id)
            return

        added = await self.storage.add_admin(target_user_id)
        if added:
            await chat.send_message(texts.dm_admin_added.format(user_id=target_user_id))
        else:
            await chat.send_message(
                texts.dm_already_admin.format(user_id=target_user_id)
            )

        context.user_data.pop("pending_admin_action", None)
        await self.analytics.record("dm.admin_panel_promote_completed")

    async def _process_admin_demote_response(
        self, update: Update, context: ApplicationContext
    ) -> None:
        message = update.message
        user = update.effective_user
        chat = update.effective_chat
        if message is None or user is None or chat is None:
            return

        if user.id != self.owner_id:
            context.user_data.pop("pending_admin_action", None)
            return

        texts = self._get_texts(context, getattr(user, "language_code", None))
        payload = (message.text or "").strip()
        try:
            target_user_id = int(payload)
        except (TypeError, ValueError):
            await message.reply_text(texts.dm_admin_invalid_user_id)
            return

        removed = await self.storage.remove_admin(target_user_id)
        if removed:
            await chat.send_message(
                texts.dm_admin_removed.format(user_id=target_user_id)
            )
        else:
            await chat.send_message(texts.dm_not_admin.format(user_id=target_user_id))

        context.user_data.pop("pending_admin_action", None)
        await self.analytics.record("dm.admin_panel_demote_completed")

    async def _notify_user(
        self, context: ApplicationContext, user_id: int, text: str
    ) -> None:
        try:
            await context.bot.send_message(chat_id=user_id, text=text)
        except Exception as exc:
            LOGGER.error("Failed to notify user %s: %s", user_id, exc)

    async def list_admins(
        self, update: Update, context: ApplicationContext
    ) -> None:
        chat = update.effective_chat
        if chat is None:
            return
        language_code = getattr(update.effective_user, "language_code", None)
        texts = self._get_texts(context, language_code)
        admins_text = self._render_admins_list(texts)
        if admins_text == texts.dm_admin_manage_list_empty:
            await chat.send_message(texts.dm_no_admins)
            return
        await chat.send_message(admins_text)

    async def promote_admin(
        self, update: Update, context: ApplicationContext
    ) -> None:
        if not await self._check_owner(update):
            return
        chat = update.effective_chat
        if chat is None:
            return
        language_code = getattr(update.effective_user, "language_code", None)
        texts = self._get_texts(context, language_code)
        if not context.args:
            await chat.send_message(texts.dm_admin_enter_user_id)
            return
        try:
            user_id = int(context.args[0])
        except ValueError:
            await chat.send_message(texts.dm_admin_invalid_user_id)
            return
        added = await self.storage.add_admin(user_id)
        if added:
            await chat.send_message(texts.dm_admin_added.format(user_id=user_id))
        else:
            await chat.send_message(texts.dm_already_admin.format(user_id=user_id))

    async def demote_admin(
        self, update: Update, context: ApplicationContext
    ) -> None:
        if not await self._check_owner(update):
            return
        chat = update.effective_chat
        if chat is None:
            return
        language_code = getattr(update.effective_user, "language_code", None)
        texts = self._get_texts(context, language_code)
        if not context.args:
            await chat.send_message(texts.dm_admin_enter_user_id)
            return
        try:
            user_id = int(context.args[0])
        except ValueError:
            await chat.send_message(texts.dm_admin_invalid_user_id)
            return
        removed = await self.storage.remove_admin(user_id)
        if removed:
            await chat.send_message(texts.dm_admin_removed.format(user_id=user_id))
        else:
            await chat.send_message(texts.dm_not_admin.format(user_id=user_id))

    async def _check_owner(self, update: Update) -> bool:
        user = update.effective_user
        chat = update.effective_chat
        if user is None or chat is None:
            return False
        if user.id != self.owner_id:
            texts = get_text_pack(getattr(user, "language_code", None))
            await chat.send_message(texts.dm_not_owner)
            return False
        return True

    async def status(self, update: Update, context: ApplicationContext) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if user is None or chat is None:
            return
        texts = self._get_texts(context, getattr(user, "language_code", None))
        text = self._render_status_text(
            self.storage.get_application_status(user.id), texts
        )
        await chat.send_message(text)
        await self.analytics.record("dm.status_requested")

    async def show_status_callback(
        self, update: Update, context: ApplicationContext
    ) -> None:
        query = update.callback_query
        if not query:
            return
        await query.answer()
        user = query.from_user
        if user is None:
            return
        message = query.message
        if message is None:
            return
        texts = self._get_texts(context, getattr(user, "language_code", None))
        text = self._render_status_text(
            self.storage.get_application_status(user.id), texts
        )
        await message.chat.send_message(text)
        await self.analytics.record("dm.status_requested")

    async def withdraw(
        self, update: Update, context: ApplicationContext
    ) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if user is None or chat is None:
            return
        texts = self._get_texts(context, getattr(user, "language_code", None))
        success = await self.storage.withdraw_application(user.id)
        context.user_data.pop("is_filling_application", None)
        context.user_data.pop("application_flow", None)
        if success:
            await chat.send_message(texts.dm_withdraw_success)
            await self.analytics.record("dm.withdraw_completed")
        else:
            await chat.send_message(texts.dm_withdraw_not_found)
            await self.analytics.record("dm.withdraw_missing")

    async def handle_withdraw_callback(
        self, update: Update, context: ApplicationContext
    ) -> None:
        query = update.callback_query
        if not query:
            return
        await query.answer()
        user = query.from_user
        if user is None:
            return
        message = query.message
        if message is None:
            return
        texts = self._get_texts(context, getattr(user, "language_code", None))
        success = await self.storage.withdraw_application(user.id)
        context.user_data.pop("is_filling_application", None)
        context.user_data.pop("application_flow", None)
        if success:
            await message.chat.send_message(texts.dm_withdraw_success)
            await self.analytics.record("dm.withdraw_completed")
        else:
            await message.chat.send_message(texts.dm_withdraw_not_found)
            await self.analytics.record("dm.withdraw_missing")

    async def show_language_menu(
        self, update: Update, context: ApplicationContext
    ) -> None:
        query = update.callback_query
        if not query:
            return
        await query.answer()
        user = query.from_user
        message = query.message
        if message is None:
            return
        texts = self._get_texts(
            context, getattr(user, "language_code", None) if user else None
        )
        active = (
            context.user_data.get("preferred_language")
            if isinstance(context.user_data, dict)
            else None
        )
        await message.edit_text(
            text=texts.dm_language_menu_title,
            reply_markup=language_options_keyboard(
                active if isinstance(active, str) else None, texts
            ),
        )
        await self.analytics.record("dm.language_menu_opened")

    async def close_language_menu(
        self, update: Update, context: ApplicationContext
    ) -> None:
        query = update.callback_query
        if not query:
            return
        await query.answer()
        message = query.message
        user = query.from_user
        if message is None:
            return
        texts = self._get_texts(
            context, getattr(user, "language_code", None) if user else None
        )
        is_admin = self._is_admin(user.id) if user else False
        await message.edit_text(
            text=self._build_welcome_text(texts),
            reply_markup=glass_dm_welcome_keyboard(
                texts,
                self._get_webapp_url(context),
                is_admin=is_admin,
            ),
        )
        await self.analytics.record("dm.language_menu_closed")

    async def set_language_callback(
        self, update: Update, context: ApplicationContext
    ) -> None:
        query = update.callback_query
        if not query:
            return
        data = query.data or ""
        parts = data.split(":", 1)
        if len(parts) != 2:
            await query.answer()
            return
        _, code = parts
        normalised = normalize_language_code(code) or code
        if isinstance(context.user_data, dict):
            context.user_data["preferred_language"] = normalised
        user = query.from_user
        new_texts = get_text_pack(normalised)
        await self.analytics.record("dm.language_updated")
        await query.answer(new_texts.dm_language_updated, show_alert=True)
        message = query.message
        if message is None:
            return
        is_admin = self._is_admin(user.id) if user else False
        await message.edit_text(
            text=self._build_welcome_text(new_texts),
            reply_markup=glass_dm_welcome_keyboard(
                new_texts,
                self._get_webapp_url(context),
                is_admin=is_admin,
            ),
        )

    def _build_welcome_text(self, texts: TextPack) -> str:
        return f"{texts.dm_welcome}\n\n{texts.glass_panel_caption}"

    def _build_admin_panel_text(self, texts: TextPack) -> str:
        return f"{texts.dm_admin_panel_intro}\n\n{texts.glass_panel_caption}"

    def _build_admin_management_text(self, texts: TextPack) -> str:
        admins_list = self._render_admins_list(texts)
        return "\n\n".join(
            [texts.dm_admin_manage_title, texts.dm_admin_manage_intro, admins_list]
        )

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
            return texts.dm_admin_manage_list_empty

        lines: List[str] = [texts.dm_admin_manage_list_header]
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
                parts.append(texts.dm_admin_manage_list_unknown)

            display = " / ".join(parts)
            safe_user_id = escape(str(user_id))
            lines.append(
                texts.dm_admin_manage_list_entry.format(
                    display=display,
                    user_id=safe_user_id,
                )
            )

        return "\n".join(lines)

    def _render_members_list(
        self,
        entries: list[tuple[int, ApplicationHistoryEntry]],
        texts: TextPack,
    ) -> str:
        if not entries:
            return texts.dm_admin_panel_members_empty

        lines = []
        for user_id, history in entries[:10]:
            updated_at = escape(getattr(history, "updated_at", ""))
            lines.append(f"• <code>{user_id}</code> – {updated_at}")
        members_block = "\n".join(lines)
        return texts.dm_admin_panel_members_header.format(
            count=len(entries),
            members=members_block,
        )

    async def _send_pending_applications(self, chat, texts: TextPack) -> bool:
        pending = self.storage.get_pending_applications()
        if not pending:
            await chat.send_message(texts.dm_no_pending)
            return False

        await self.analytics.record("dm.admin_pending_list")

        for application in pending[:5]:
            await chat.send_message(
                text=self._format_application_entry(application, texts),
                reply_markup=application_review_keyboard(application.user_id, texts),
            )
        return True

    def _is_admin(self, user_id: int) -> bool:
        checker = getattr(self.storage, "is_admin", None)
        if callable(checker):
            return bool(checker(user_id))
        return False

    def _get_application_status(self, user_id: int) -> ApplicationHistoryEntry | None:
        getter = getattr(self.storage, "get_application_status", None)
        if callable(getter):
            return getter(user_id)
        return None

    def _get_webapp_url(self, context: ApplicationContext) -> str | None:
        bot_data = getattr(context, "bot_data", None)
        if isinstance(bot_data, dict):
            url = bot_data.get("webapp_url")
            if isinstance(url, str) and url:
                return url
        return None

    def _render_application_text(
        self, user_id: int, texts: TextPack | None = None
    ) -> str:
        application = self.storage.get_application(user_id)
        text_pack = texts or get_default_text_pack()
        if not application:
            return text_pack.dm_no_pending
        return self._format_application_entry(application, text_pack)

    def _format_application_entry(
        self, application: Application, texts: TextPack
    ) -> str:
        full_name = escape(str(application.full_name))
        username = application.username
        if username:
            username = username.lstrip("@")
            username_display = f"@{username}" if username else "—"
        else:
            username_display = "—"
        username_escaped = escape(username_display)
        answers_block = self._format_application_answers(application, texts)
        created_at = escape(str(application.created_at))
        return texts.dm_application_item.format(
            full_name=full_name,
            username=username_escaped,
            user_id=application.user_id,
            answers=answers_block,
            created_at=created_at,
        )

    def _render_status_text(
        self, status: ApplicationHistoryEntry | None, texts: TextPack | None = None
    ) -> str:
        text_pack = texts or get_default_text_pack()
        if not status:
            return text_pack.dm_status_none

        status_map = {
            "pending": text_pack.dm_status_pending,
            "approved": text_pack.dm_status_approved,
            "denied": text_pack.dm_status_denied,
            "withdrawn": text_pack.dm_status_withdrawn,
        }

        status_label = status_map.get(status.status)
        if not status_label:
            status_label = text_pack.dm_status_unknown.format(
                status=escape(status.status)
            )

        updated_at = escape(status.updated_at)
        last_updated_label = text_pack.dm_status_last_updated_label

        if status.note:
            note = escape(status.note)
            return text_pack.dm_status_template_with_note.format(
                status=status_label,
                updated_at=updated_at,
                note=note,
                last_updated_label=last_updated_label,
            )

        return text_pack.dm_status_template.format(
            status=status_label,
            updated_at=updated_at,
            last_updated_label=last_updated_label,
        )

    def _get_texts(
        self,
        context: ApplicationContext,
        language_code: str | None = None,
    ) -> TextPack:
        user_data = getattr(context, "user_data", None)
        stored_language: str | None = None
        stored_pack: TextPack | None = None
        if isinstance(user_data, dict):
            maybe_stored = user_data.get("preferred_language")
            if isinstance(maybe_stored, str):
                normalised_stored = (
                    normalize_language_code(maybe_stored) or maybe_stored
                )
                if normalised_stored in AVAILABLE_LANGUAGE_CODES:
                    stored_language = normalised_stored
                    stored_pack = get_text_pack(stored_language)
                    if normalised_stored != maybe_stored:
                        user_data["preferred_language"] = normalised_stored

        if stored_pack:
            return stored_pack

        if isinstance(user_data, dict) and "preferred_language" not in user_data:
            user_data["preferred_language"] = DEFAULT_LANGUAGE_CODE
            return get_default_text_pack()

        normalised = normalize_language_code(language_code)
        if normalised and normalised in AVAILABLE_LANGUAGE_CODES:
            return get_text_pack(normalised)

        return get_default_text_pack()

    def _get_active_language_code(
        self,
        context: ApplicationContext,
        language_code: str | None = None,
    ) -> str | None:
        stored: str | None = None
        if isinstance(context.user_data, dict):
            maybe_stored = context.user_data.get("preferred_language")
            if isinstance(maybe_stored, str):
                stored = normalize_language_code(maybe_stored) or maybe_stored

        if stored:
            return stored

        requested = normalize_language_code(language_code) if language_code else None
        if requested:
            return requested

        return DEFAULT_LANGUAGE_CODE

    def _get_language_label(self, texts: TextPack, language_code: str | None) -> str:
        language_names = getattr(texts, "language_names", {})
        if language_code and language_code in language_names:
            return language_names[language_code]
        if language_code:
            return language_code
        if language_names:
            return next(iter(language_names.values()))
        return "—"

    def _get_question_overrides(self, language_code: str | None) -> Dict[str, str]:
        getter = getattr(self.storage, "get_application_questions", None)
        if not callable(getter):
            return {}
        try:
            overrides = getter(language_code)
        except TypeError:
            overrides = getter()  # type: ignore[misc]
        except Exception:  # pragma: no cover - defensive logging
            LOGGER.exception("Failed to load question overrides")
            return {}
        if not isinstance(overrides, dict):
            return {}
        return {
            str(question_id): str(prompt)
            for question_id, prompt in overrides.items()
            if isinstance(question_id, str) and isinstance(prompt, str)
        }

    def _resolve_question_prompt(
        self,
        question_id: str,
        texts: TextPack,
        language_code: str | None,
    ) -> str:
        overrides = self._get_question_overrides(language_code)
        prompt = overrides.get(question_id)
        if prompt:
            return prompt

        if question_id == "role_prompt":
            return texts.dm_application_role_prompt
        if question_id == "goals_prompt":
            return texts.dm_application_goals_prompt
        if question_id == "availability_prompt":
            return texts.dm_application_availability_prompt
        if question_id.startswith("followup_"):
            role_key = question_id.split("_", 1)[1]
            return texts.dm_application_followup_prompts.get(role_key, "")
        return ""

    def _get_question_label(self, question_id: str, texts: TextPack) -> str:
        if question_id == "role_prompt":
            return texts.dm_admin_questions_role_label
        if question_id == "goals_prompt":
            return texts.dm_admin_questions_goals_label
        if question_id == "availability_prompt":
            return texts.dm_admin_questions_availability_label
        if question_id.startswith("followup_"):
            role_key = question_id.split("_", 1)[1]
            template = getattr(
                texts,
                "dm_admin_questions_followup_label_template",
                "{role}",
            )
            options = texts.dm_application_role_options.get(role_key, [])
            role_label = options[0] if options else role_key
            return template.format(role=role_label)
        return question_id

    async def _handle_application_flow_step(
        self,
        update: Update,
        context: ApplicationContext,
        texts: TextPack,
        answer: str,
        flow_state: dict,
        language_code: str | None,
    ) -> bool:
        message = update.message
        if message is None:
            return False

        responses = flow_state.setdefault("answers", [])
        answered_values: Dict[str, str] = flow_state.setdefault("answered_values", {})
        form_definitions = parse_form(flow_state.get("form", []))

        current_question_id = flow_state.get("pending_question_id")
        if not current_question_id:
            next_question = self._select_next_question(
                form_definitions, answered_values
            )
            if next_question is None:
                await message.reply_text(texts.dm_application_no_questions)
                return True
            flow_state["pending_question_id"] = next_question.question_id
            await message.reply_text(next_question.prompt)
            return False

        current_question = self._find_question(form_definitions, current_question_id)
        if current_question is None:
            next_question = self._select_next_question(
                form_definitions, answered_values
            )
            if next_question is None:
                await message.reply_text(texts.dm_application_no_questions)
                return True
            flow_state["pending_question_id"] = next_question.question_id
            await message.reply_text(next_question.prompt)
            return False

        provided_answer = answer.strip()
        canonical_value: Optional[str]
        display_answer: str

        if current_question.kind == "choice":
            match = self._match_option_answer(current_question, provided_answer)
            if match is None:
                options = ", ".join(self._format_option_labels(current_question))
                await message.reply_text(
                    texts.dm_application_invalid_choice.format(options=options)
                )
                return False
            canonical_value, display_answer = match
        else:
            if current_question.required and not provided_answer:
                await message.reply_text(texts.dm_application_required)
                return False
            canonical_value = provided_answer
            display_answer = provided_answer

        responses.append(
            {
                "question_id": current_question.question_id,
                "question": current_question.prompt,
                "answer": display_answer,
            }
        )
        answered_values[current_question.question_id] = canonical_value or ""

        next_question = self._select_next_question(form_definitions, answered_values)
        if next_question is not None:
            flow_state["pending_question_id"] = next_question.question_id
            await message.reply_text(next_question.prompt)
            return False

        flow_state["pending_question_id"] = None
        application_responses = [
            ApplicationResponse(
                question_id=item["question_id"],
                question=item["question"],
                answer=item["answer"],
            )
            for item in responses
        ]
        summary_text = self._format_application_summary(application_responses, texts)
        aggregated_answer = self._collapse_responses(application_responses)

        user = update.effective_user
        if user is None:
            return False

        try:
            async with self.analytics.track_time("dm.application_store"):
                success = await self.storage.add_application(
                    user_id=user.id,
                    full_name=user.full_name or user.username or str(user.id),
                    username=getattr(user, "username", None),
                    answer=aggregated_answer,
                    language_code=context.user_data.get("preferred_language"),
                    responses=application_responses,
                )
        except Exception as exc:
            LOGGER.error("Failed to persist application for %s: %s", user.id, exc)
            await self.analytics.record("dm.application_error")
            await message.reply_text(texts.error_generic)
            return True

        if not success:
            LOGGER.warning("Duplicate application prevented for user %s", user.id)
            await message.reply_text(texts.dm_application_duplicate)
            return True

        await message.reply_text(summary_text)
        await message.reply_text(texts.dm_application_received)
        await self.analytics.record("dm.application_submitted")

        review_chat_id = context.bot_data.get("review_chat_id")
        if review_chat_id:
            await context.bot.send_message(
                chat_id=review_chat_id,
                text=self._render_application_text(user.id),
                reply_markup=application_review_keyboard(
                    user.id, get_default_text_pack()
                ),
            )
        return True

    def _get_application_form(
        self, language_code: str | None
    ) -> List[ApplicationQuestionDefinition]:
        getter = getattr(self.storage, "get_application_form", None)
        if not callable(getter):
            return []
        try:
            definitions = getter(language_code)
        except TypeError:
            definitions = getter()
        except Exception:  # pragma: no cover - defensive logging
            LOGGER.exception("Failed to load application form definitions")
            return []

        result: List[ApplicationQuestionDefinition] = []
        for item in definitions or []:
            if isinstance(item, ApplicationQuestionDefinition):
                result.append(item)
            elif isinstance(item, dict):
                try:
                    result.append(ApplicationQuestionDefinition.from_dict(item))
                except Exception:  # pragma: no cover - defensive logging
                    LOGGER.warning("Invalid application question definition skipped")
        return result

    def _find_question(
        self,
        definitions: List[ApplicationQuestionDefinition],
        question_id: str,
    ) -> ApplicationQuestionDefinition | None:
        for definition in definitions:
            if definition.question_id == question_id:
                return definition
        return None

    def _select_next_question(
        self,
        definitions: List[ApplicationQuestionDefinition],
        answered: Dict[str, str],
    ) -> ApplicationQuestionDefinition | None:
        for definition in definitions:
            if definition.question_id in answered:
                continue
            if definition.depends_on:
                dependency_value = answered.get(definition.depends_on)
                if dependency_value is None:
                    continue
                if (
                    definition.depends_value
                    and dependency_value != definition.depends_value
                ):
                    continue
            return definition
        return None

    def _format_option_labels(
        self, question: ApplicationQuestionDefinition
    ) -> List[str]:
        labels: List[str] = []
        for option in question.options:
            label = option.label or option.value
            labels.append(label)
        return labels

    def _match_option_answer(
        self, question: ApplicationQuestionDefinition, answer: str
    ) -> tuple[str, str] | None:
        if not answer:
            return None
        for option in question.options:
            if option.matches(answer):
                label = option.label or option.value
                return option.value, label
        return None

    def _format_application_answers(
        self, application: Application, texts: TextPack
    ) -> str:
        if application.responses:
            lines = [
                texts.dm_application_summary_item.format(
                    question=escape(response.question),
                    answer=escape(response.answer) if response.answer else "—",
                )
                for response in application.responses
            ]
            return "\n".join(lines)

        raw_answer = application.answer if application.answer else "—"
        return escape(str(raw_answer))

    def _format_application_summary(
        self, responses: List[ApplicationResponse], texts: TextPack
    ) -> str:
        lines = [texts.dm_application_summary_title]
        for response in responses:
            lines.append(
                texts.dm_application_summary_item.format(
                    question=escape(response.question),
                    answer=escape(response.answer) if response.answer else "—",
                )
            )
        return "\n".join(lines)

    def _collapse_responses(self, responses: List[ApplicationResponse]) -> str:
        return "\n".join(
            f"{response.question.strip()} {response.answer.strip()}".strip()
            for response in responses
        )

    def _render_admin_insights(self, stats: Dict[str, Any], texts: TextPack) -> str:
        pending = int(stats.get("pending", 0))
        status_counts = stats.get("status_counts", {}) or {}
        approved = int(status_counts.get("approved", 0))
        denied = int(status_counts.get("denied", 0))
        withdrawn = int(status_counts.get("withdrawn", 0))
        total = int(stats.get("total", 0))
        average_length = float(stats.get("average_pending_answer_length", 0.0))

        counts_block = texts.dm_admin_panel_insights_counts.format(
            pending=pending,
            approved=approved,
            denied=denied,
            withdrawn=withdrawn,
            total=total,
            average_length=average_length,
        )

        languages = stats.get("languages", {}) or {}
        if languages:
            language_lines = [
                f"• {escape(str(code))}: {count}"
                for code, count in sorted(
                    languages.items(), key=lambda item: (-int(item[1]), str(item[0]))
                )
            ]
            languages_block = texts.dm_admin_panel_insights_languages.format(
                languages="\n".join(language_lines)
            )
        else:
            languages_block = texts.dm_admin_panel_insights_languages_empty

        recent_updates = stats.get("recent_updates", []) or []
        if recent_updates:
            recent_lines = []
            for entry in recent_updates:
                user_id = escape(str(entry.get("user_id", "—")))
                status = escape(str(entry.get("status", "")))
                updated_at = escape(str(entry.get("updated_at", "")))
                recent_lines.append(
                    f"• <code>{user_id}</code> – {status} ({updated_at})"
                )
            recent_block = texts.dm_admin_panel_insights_recent.format(
                items="\n".join(recent_lines)
            )
        else:
            recent_block = texts.dm_admin_panel_insights_recent_empty

        return "\n".join(
            [
                texts.dm_admin_panel_insights_title,
                counts_block,
                languages_block,
                recent_block,
            ]
        )
