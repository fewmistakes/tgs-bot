from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import sys
from collections.abc import Iterable

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat

from config import (
    BASE_DIR,
    DB_PATH,
    PROFILE_DB_PATH,
    PROMO_DB_PATH,
    REPORTS_DB_PATH,
    Config,
    load_config,
)
from db import DB
from handlers import configs, convert, main_menu, pack, settings, wallet, watermark
from handlers.admin import (
    broadcast as admin_broadcast,
    content as admin_content,
    panel as admin_panel,
)
from services import content as content_service
from services.billing import Billing
from services.payments.cryptobot import CryptoBot
from services.payments.platega import Platega, watch_payments as watch_platega_payments
from services.profile_db import ProfileDB
from services.promo_db import PromoDB
from services.reports_db import ReportsDB
from services.webapp_server import run_webapp
from utils.logger import get_logger, setup_logging
from utils.middleware import DIMiddleware

log = get_logger("bot")

PUBLIC_COMMANDS = [BotCommand(command="start", description="Запустить бота")]
ADMIN_COMMANDS = [
    *PUBLIC_COMMANDS,
    BotCommand(command="admin", description="Панель администратора"),
]

# Order matters: specific callback handlers must be registered before the
# generic ones, and the sticker catch-all in `convert` has to stay last.
ROUTERS = (
    main_menu.router,
    admin_content.router,
    admin_broadcast.router,
    admin_panel.router,
    settings.router,
    configs.router,
    watermark.router,
    wallet.router,
    pack.router,
    convert.router,
)


async def set_commands(bot: Bot, admin_ids: Iterable[int]) -> None:
    await bot.set_my_commands(PUBLIC_COMMANDS)
    for admin_id in admin_ids:
        # Fails for admins who have never opened a chat with the bot.
        with contextlib.suppress(Exception):
            await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))


def log_config(cfg: Config) -> None:
    encoder = (
        f"MP4 (crf={cfg.h264_crf} preset={cfg.h264_preset} maxrate={cfg.h264_max_kbps}k)"
        if cfg.output_format == "mp4"
        else cfg.output_format.upper()
    )
    log.info(
        "config: %dx%d @ %dfps, format=%s, price=%d₽, free=%d, admins=%s",
        cfg.output_width,
        cfg.output_height,
        cfg.output_fps,
        encoder,
        cfg.default_render_price,
        cfg.default_free_renders,
        sorted(cfg.admin_ids),
    )


async def main() -> None:
    setup_logging(level=logging.INFO)
    cfg = load_config()
    log_config(cfg)

    db = DB(DB_PATH)
    profile_db = ProfileDB(PROFILE_DB_PATH)
    promo_db = PromoDB(PROMO_DB_PATH)
    reports_db = ReportsDB(REPORTS_DB_PATH)
    databases = (db, profile_db, promo_db, reports_db)
    for database in databases:
        await database.open()

    billing = Billing(db, cfg.default_render_price, cfg.default_free_renders)
    cryptobot = CryptoBot(cfg.cryptobot_token, cfg.cryptobot_testnet)
    platega = Platega(cfg.platega_merchant_id, cfg.platega_token)

    content_service.init(db)
    await content_service.service().load()

    bot = Bot(token=cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    if not cfg.bot_username and me.username:
        cfg = dataclasses.replace(cfg, bot_username=me.username)
    log.info("logged in as @%s (id=%d)", me.username, me.id)

    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(
        DIMiddleware(cfg, db, billing, cryptobot, platega, profile_db, promo_db, reports_db)
    )
    dp.include_routers(*ROUTERS)
    await set_commands(bot, cfg.admin_ids)

    platega_task = asyncio.create_task(
        watch_platega_payments(
            bot=bot, db=db, billing=billing, platega=platega, reports_db=reports_db
        ),
        name="platega-watcher",
    )

    webapp_runner = webapp_site = None
    try:
        webapp_runner, webapp_site = await run_webapp(
            master_pack_dir=BASE_DIR / "master_pack",
            new_pack_dir=BASE_DIR / "emoji_stickers_swex",
            webapp_dir=BASE_DIR / "webapp",
            host=cfg.webapp_host,
            port=cfg.webapp_port,
            bot=bot,
            cfg=cfg,
            db=db,
            billing=billing,
            profile_db=profile_db,
        )
    except Exception:
        log.exception("webapp server failed to start, continuing without it")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        platega_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await platega_task
        if webapp_site is not None:
            with contextlib.suppress(Exception):
                await webapp_site.stop()
        if webapp_runner is not None:
            with contextlib.suppress(Exception):
                await webapp_runner.cleanup()
        await bot.session.close()
        for database in databases:
            await database.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
