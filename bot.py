from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from flyzexbot.config import Settings
from flyzexbot.handlers.dm import DMHandlers
from flyzexbot.handlers.group import GroupHandlers
from flyzexbot.localization import get_default_text_pack
from flyzexbot.services.analytics import AnalyticsTracker
from flyzexbot.services.security import RateLimitGuard
from flyzexbot.services.storage import Storage, configure_timezone
from flyzexbot.rubika import RubikaAPI, RubikaApplication

CONFIG_PATH = Path("config/settings.yaml")


async def setup_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.logging.level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if settings.logging.file:
        settings.logging.file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(settings.logging.file, encoding="utf-8")
        file_handler.setLevel(getattr(logging, settings.logging.level.upper(), logging.INFO))
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logging.getLogger().addHandler(file_handler)


async def build_application(settings: Settings) -> None:
    await setup_logging(settings)

    analytics = AnalyticsTracker(settings.analytics.flush_interval)
    await analytics.start()

    # Apply configured timezone for timestamp formatting
    try:
        configure_timezone(getattr(settings, "system").timezone)
    except Exception:
        logging.getLogger(__name__).warning("Failed to apply configured timezone; using default.")

    storage = Storage(
        settings.storage.path,
        backup_path=settings.storage.backup_path,
    )
    await storage.load()

    if settings.telegram.owner_id not in storage.list_admins():
        await storage.add_admin(settings.telegram.owner_id)

    rate_limiter = RateLimitGuard(settings.security.rate_limit_interval, settings.security.rate_limit_burst)

    dm_handlers = DMHandlers(
        storage=storage,
        owner_id=settings.telegram.owner_id,
        analytics=analytics,
        rate_limiter=rate_limiter,
    )
    group_handlers = GroupHandlers(
        storage=storage,
        xp_per_character=settings.xp.message_character_reward,
        xp_message_limit=settings.xp.message_reward_limit,
        xp_limit=settings.xp.leaderboard_size,
        cups_limit=settings.cups.leaderboard_size,
        milestone_interval=settings.xp.milestone_interval,
        message_cooldown_seconds=settings.xp.message_reward_cooldown,
        analytics=analytics,
    )

    api = RubikaAPI(settings.get_bot_token())
    application = RubikaApplication(api)

    application.bot_data["review_chat_id"] = settings.telegram.application_review_chat
    application.bot_data["analytics"] = analytics
    application.bot_data["storage_path"] = str(settings.storage.path)
    webapp_url = settings.webapp.get_url()
    if webapp_url:
        application.bot_data["webapp_url"] = webapp_url

    for handler in dm_handlers.build_handlers():
        application.add_handler(handler)

    for handler in group_handlers.build_handlers():
        application.add_handler(handler)

    logging.info("FlyzexBot Rubika adapter is running with polling mode.")
    try:
        await application.run_polling()
    finally:
        await api.close()
        await storage.save()
        await analytics.stop()


async def main() -> None:
    config_path = CONFIG_PATH if CONFIG_PATH.exists() else Path("config/settings.example.yaml")
    settings = Settings.load(config_path)
    await build_application(settings)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
