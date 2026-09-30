import tempfile
import unittest
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.access import UserAccessMiddleware
from bot.admin import AdminControl, build_admin_router, managed_service_name
from bot.database import Database
from bot.service import ScheduleService


class AccessControlsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "bot.sqlite3"
        self.db = Database(self.path)
        await self.db.initialize()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.directory.cleanup()

    async def test_maintenance_survives_database_restart(self) -> None:
        self.assertEqual(await self.db.access_mode(), "normal")
        await self.db.set_access_mode("maintenance")
        await self.db.close()
        self.db = Database(self.path)
        await self.db.initialize()
        self.assertEqual(await self.db.access_mode(), "maintenance")

    async def test_maintenance_ignores_users_but_keeps_admin_updates(self) -> None:
        middleware = UserAccessMiddleware(self.db, {959026123})
        handler = AsyncMock(return_value="handled")
        user_event = SimpleNamespace(event=SimpleNamespace(
            from_user=SimpleNamespace(id=42)
        ))
        admin_event = SimpleNamespace(event=SimpleNamespace(
            from_user=SimpleNamespace(id=959026123)
        ))
        self.assertEqual(await middleware(handler, user_event, {}), "handled")
        await self.db.set_access_mode("maintenance")
        self.assertIsNone(await middleware(handler, user_event, {}))
        self.assertEqual(await middleware(handler, admin_event, {}), "handled")
        self.assertEqual(handler.await_count, 2)

    async def test_automatic_messages_skip_users_but_allow_admin(self) -> None:
        bot = SimpleNamespace(send_message=AsyncMock())
        service = ScheduleService(
            bot, self.db, SimpleNamespace(), None, SimpleNamespace(),
            admin_ids={959026123},
        )
        await self.db.set_access_mode("maintenance")
        self.assertFalse(await service.safe_send(42, "automatic"))
        self.assertTrue(await service.safe_send(959026123, "automatic"))
        bot.send_message.assert_awaited_once_with(959026123, "automatic")
        await self.db.set_access_mode("normal")
        self.assertTrue(await service.safe_send(42, "automatic"))

    async def test_full_shutdown_requires_two_buttons_and_exact_phrase(self) -> None:
        admin_id = 959026123
        router = build_admin_router(SimpleNamespace(), self.db, {admin_id}, SimpleNamespace())
        handlers = {
            handler.callback.__name__: handler.callback
            for handler in router.callback_query.handlers + router.message.handlers
        }
        state = FSMContext(
            MemoryStorage(), StorageKey(bot_id=1, chat_id=admin_id, user_id=admin_id)
        )
        message = SimpleNamespace(
            text="not the phrase", from_user=SimpleNamespace(id=admin_id),
            answer=AsyncMock(), edit_text=AsyncMock(),
        )
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=admin_id), message=message,
            answer=AsyncMock(),
        )
        marker = self.path.parent / "bot-disabled"

        with patch.dict(os.environ, {"BOT_SERVICE_NAME": "university-schedule-dev.service"}), \
             patch("bot.admin.Path.cwd", return_value=Path("/home/papaya/UniversitySchedule-dev")):
            await handlers["request_shutdown"](callback, state)
            self.assertEqual(await state.get_state(), AdminControl.shutdown_first.state)
            await handlers["confirm_shutdown_first"](callback, state)
            self.assertEqual(await state.get_state(), AdminControl.shutdown_second.state)
            await handlers["confirm_shutdown_second"](callback, state)
            self.assertEqual(await state.get_state(), AdminControl.shutdown_phrase.state)
            self.assertFalse(marker.exists())
            await handlers["finish_shutdown"](message, state)
            self.assertFalse(marker.exists())

            process = SimpleNamespace(wait=AsyncMock(return_value=0))
            message.text = "ОТКЛЮЧИТЬ БОТА"
            with patch("bot.admin.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as command:
                await handlers["finish_shutdown"](message, state)
            self.assertTrue(marker.exists())
            self.assertEqual(command.await_count, 2)
            self.assertIsNone(await state.get_state())

    def test_dev_process_cannot_target_production_service(self) -> None:
        with patch.dict(os.environ, {"BOT_SERVICE_NAME": "university-schedule.service"}), \
             patch("bot.admin.Path.cwd", return_value=Path("/home/papaya/UniversitySchedule-dev")):
            self.assertIsNone(managed_service_name())

    async def test_shutdown_can_be_cancelled_before_final_phrase(self) -> None:
        admin_id = 959026123
        router = build_admin_router(SimpleNamespace(), self.db, {admin_id}, SimpleNamespace())
        cancel = next(
            handler.callback for handler in router.message.handlers
            if handler.callback.__name__ == "cancel_shutdown"
        )
        state = FSMContext(
            MemoryStorage(), StorageKey(bot_id=1, chat_id=admin_id, user_id=admin_id)
        )
        await state.set_state(AdminControl.shutdown_phrase)
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=admin_id), answer=AsyncMock()
        )
        await cancel(message, state)
        self.assertIsNone(await state.get_state())
        self.assertFalse((self.path.parent / "bot-disabled").exists())
