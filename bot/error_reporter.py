import html
import logging
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot

from bot.database import Database

logger = logging.getLogger(__name__)


def is_message_not_modified(error: BaseException) -> bool:
    return "message is not modified" in str(error).lower()


class ErrorReporter:
    def __init__(
        self, bot: Bot, database: Database, admin_ids: set[int], timezone: ZoneInfo
    ) -> None:
        self.bot = bot
        self.db = database
        self.admin_ids = admin_ids
        self.timezone = timezone

    async def report(self, context: str, error: BaseException) -> None:
        logger.error("%s: %s", context, error, exc_info=error)
        try:
            await self.db.record_error(context, error)
        except Exception:
            logger.exception("Could not store error")

        trace = "".join(traceback.format_exception(error))
        text = (
            "🚨 <b>Ошибка в боте</b>\n\n"
            f"<b>Время:</b> {datetime.now(self.timezone):%d.%m.%Y %H:%M:%S}\n"
            f"<b>Контекст:</b> {html.escape(context)}\n"
            f"<b>Тип:</b> {html.escape(type(error).__name__)}\n"
            f"<b>Сообщение:</b> {html.escape(str(error) or 'без сообщения')}\n\n"
            f"<pre>{html.escape(trace[-2800:])}</pre>"
        )
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(admin_id, text)
            except Exception:
                # Reporting an error must never create an error-reporting loop.
                logger.exception("Could not notify admin %s", admin_id)
