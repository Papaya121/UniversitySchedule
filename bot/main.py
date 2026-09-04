import asyncio
import logging
from datetime import datetime, time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeChat, ErrorEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.admin import build_admin_router
from bot.config import Settings
from bot.database import Database
from bot.error_reporter import ErrorReporter, is_message_not_modified
from bot.handlers import build_router
from bot.schedule_client import ScheduleClient
from bot.service import ScheduleService


async def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    database = Database(settings.database_path)
    await database.initialize(settings.group_name)
    client = ScheduleClient(settings.schedule_url)
    reporter = ErrorReporter(bot, database, settings.admin_ids, settings.tz)
    service = ScheduleService(bot, database, client, settings.tz, reporter)

    dispatcher = Dispatcher()
    dispatcher.include_router(build_admin_router(bot, database, settings.admin_ids, reporter))
    dispatcher.include_router(build_router(database, service, reporter))

    @dispatcher.error()
    async def dispatcher_error(event: ErrorEvent) -> bool:
        if is_message_not_modified(event.exception):
            return True
        await reporter.report("Необработанная ошибка диспетчера", event.exception)
        return True

    loop = asyncio.get_running_loop()

    def asyncio_error(_loop, context: dict) -> None:
        error = context.get("exception") or RuntimeError(context.get("message", "Asyncio error"))
        _loop.create_task(reporter.report("Фоновая ошибка asyncio", error))

    loop.set_exception_handler(asyncio_error)
    scheduler = AsyncIOScheduler(timezone=settings.tz)
    scheduler.add_job(
        service.check_changes,
        "interval",
        minutes=settings.check_interval_minutes,
        id="schedule_changes",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        service.send_morning,
        "cron",
        hour=settings.morning_hour,
        minute=settings.morning_minute,
        id="morning_schedule",
        misfire_grace_time=1800,
    )
    scheduler.add_job(
        service.send_tomorrow_after_last_lesson,
        "cron",
        minute="*",
        id="tomorrow_schedule",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        service.send_next_lesson,
        "cron",
        minute="*",
        id="next_lesson",
        max_instances=1,
        coalesce=True,
    )

    common_commands = [
        BotCommand(command="today", description="Расписание на сегодня"),
        BotCommand(command="tomorrow", description="Расписание на завтра"),
        BotCommand(command="week", description="Расписание на неделю"),
        BotCommand(command="settings", description="Настройки профиля"),
        BotCommand(command="myid", description="Показать мой Telegram ID"),
        BotCommand(command="help", description="Помощь"),
    ]
    try:
        await bot.set_my_commands(common_commands)
    except Exception as error:
        await reporter.report("Настройка основного меню команд", error)
    for admin_id in settings.admin_ids:
        try:
            await bot.set_my_commands(
                common_commands + [BotCommand(command="admin", description="Панель администратора")],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception as error:
            await reporter.report(f"Настройка меню администратора {admin_id}", error)
    scheduler.start()
    await service.check_changes()
    now = datetime.now(settings.tz)
    morning_at = time(settings.morning_hour, settings.morning_minute)
    # If the process restarted shortly after 09:00, do not lose today's mailing.
    if morning_at <= now.time() < time(12, 0):
        await service.send_morning()
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await client.close()
        await database.close()
        await bot.session.close()


def run() -> None:
    asyncio.run(main())
