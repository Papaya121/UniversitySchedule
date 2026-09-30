"""Emergency access controls shared by updates and scheduled sends."""

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Update

from bot.database import Database


class UserAccessMiddleware(BaseMiddleware):
    def __init__(self, database: Database, admin_ids: set[int]) -> None:
        self.database = database
        self.admin_ids = admin_ids

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event.event, "from_user", None)
        if user is not None and user.id not in self.admin_ids:
            if await self.database.access_mode() == "maintenance":
                return None
        return await handler(event, data)
