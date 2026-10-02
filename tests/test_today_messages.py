import asyncio
import tempfile
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import EditMessageText

from bot.database import Database
from bot.models import DaySchedule, Lesson
from bot.service import ScheduleService


class TodayMessageTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.directory.name) / "test.sqlite3")
        await self.db.initialize()
        await self.db.upsert_user(42, "А", 1, "Иван", None)
        self.bot = SimpleNamespace(edit_message_text=AsyncMock(), send_message=AsyncMock())
        self.reporter = SimpleNamespace(report=AsyncMock())
        self.service = ScheduleService(self.bot, self.db, SimpleNamespace(),
                                       ZoneInfo("Europe/Moscow"), self.reporter)
        self.clock_patch = patch("bot.service.datetime")
        self.clock = self.clock_patch.start()
        self.now = datetime(2026, 10, 2, 8, tzinfo=self.service.timezone)
        self.clock.now.return_value = self.now
        self.schedule = DaySchedule(self.now.date(), (
            Lesson(time(8), time(9, 30), "Математика", None),
            Lesson(time(10), time(11, 30), "Физика", None),
        ))
        self.message = SimpleNamespace(chat=SimpleNamespace(id=42), answer=AsyncMock(
            side_effect=[SimpleNamespace(message_id=i) for i in range(1, 10)]
        ))

    async def asyncTearDown(self) -> None:
        self.clock_patch.stop()
        await self.db.close()
        self.directory.cleanup()

    async def send(self) -> None:
        await self.service.send_today(self.message, self.schedule, await self.db.get_user(42))

    async def morning(self) -> None:
        self.service.for_day = AsyncMock(return_value=self.schedule)
        self.bot.send_message.return_value = SimpleNamespace(message_id=100)
        with patch("bot.service.morning_delivery_time", return_value=self.now):
            await self.service.send_morning()

    def advance(self, hour, minute=0) -> None:
        self.clock.now.return_value = self.now.replace(hour=hour, minute=minute)

    async def test_replaces_previous_and_tracks_only_latest(self) -> None:
        await self.send()
        self.advance(10)
        await self.send()
        edit = self.bot.edit_message_text.call_args.kwargs
        self.assertEqual(edit["message_id"], 1)
        self.assertNotIn("Математика</b>", edit["text"])
        self.assertNotIn("Физика</b>", edit["text"])
        self.assertIn("Физика</b>", self.message.answer.call_args.args[0])
        self.assertEqual([r["message_id"] for r in await self.db.today_messages(42)], [2])

    async def test_morning_updates_and_today_removes_its_highlight(self) -> None:
        await self.morning()
        sent_text = self.bot.send_message.call_args.args[1]
        self.assertTrue(sent_text.startswith("☀️ Доброе утро!\n\n"))
        self.assertIn("Математика</b>", sent_text)
        self.advance(9, 30)
        await self.service.refresh_today_messages()
        updated = self.bot.edit_message_text.call_args.kwargs
        self.assertEqual(updated["message_id"], 100)
        self.assertTrue(updated["text"].startswith("☀️ Доброе утро!\n\n"))
        self.assertIn("Физика</b>", updated["text"])
        await self.send()
        cleaned = self.bot.edit_message_text.call_args.kwargs
        self.assertEqual(cleaned["message_id"], 100)
        self.assertTrue(cleaned["text"].startswith("☀️ Доброе утро!\n\n"))
        self.assertNotIn("Физика</b>", cleaned["text"])
        self.assertEqual([r["message_id"] for r in await self.db.today_messages(42)], [1])
        self.assertIn("Физика</b>", self.message.answer.call_args.args[0])
        # A repeated morning tick must not replace the user's newer message.
        await self.morning()
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.assertEqual([r["message_id"] for r in await self.db.today_messages(42)], [1])

    async def test_morning_respects_disabled_highlight(self) -> None:
        await self.db.toggle_highlight_current(42)
        await self.morning()
        self.assertNotIn("Математика</b>", self.bot.send_message.call_args.args[1])
        self.advance(10)
        await self.service.refresh_today_messages()
        self.bot.edit_message_text.assert_not_awaited()

    async def test_failed_morning_delivery_keeps_previous_and_retries(self) -> None:
        await self.send()
        self.bot.send_message.side_effect = TelegramNetworkError(
            method=EditMessageText(text="x"), message="Temporary error")
        await self.morning()
        self.assertEqual([r["message_id"] for r in await self.db.today_messages(42)], [1])
        self.bot.edit_message_text.assert_not_awaited()
        self.bot.send_message.side_effect = None
        await self.morning()
        self.assertEqual([r["message_id"] for r in await self.db.today_messages(42)], [100])

    async def test_morning_restart_keeps_greeting(self) -> None:
        await self.morning()
        await self.db.close()
        self.db = Database(Path(self.directory.name) / "test.sqlite3")
        await self.db.initialize()
        self.service = ScheduleService(self.bot, self.db, SimpleNamespace(),
                                       self.now.tzinfo, self.reporter)
        self.advance(10)
        await self.service.refresh_today_messages()
        text = self.bot.edit_message_text.call_args.kwargs["text"]
        self.assertTrue(text.startswith("☀️ Доброе утро!\n\n"))
        self.assertIn("Физика</b>", text)

    async def test_timer_edits_only_on_change_and_stops_after_last_lesson(self) -> None:
        await self.send()
        await self.service.refresh_today_messages()
        self.bot.edit_message_text.assert_not_awaited()
        self.advance(9, 30)
        await self.service.refresh_today_messages()
        self.assertIn("Физика</b>", self.bot.edit_message_text.call_args.kwargs["text"])
        self.advance(10)
        await self.service.refresh_today_messages()
        self.assertEqual(self.bot.edit_message_text.await_count, 1)
        self.advance(11, 30)
        await self.service.refresh_today_messages()
        self.assertNotIn("Физика</b>", self.bot.edit_message_text.call_args.kwargs["text"])
        self.bot.send_message.assert_not_awaited()

    async def test_before_first_lesson_threshold(self) -> None:
        self.advance(7)
        await self.send()
        self.advance(7, 30)
        await self.service.refresh_today_messages()
        self.assertIn("Математика</b>", self.bot.edit_message_text.call_args.kwargs["text"])

    async def test_toggle_removes_and_restores_highlight(self) -> None:
        await self.send()
        await self.db.toggle_highlight_current(42)
        await self.service.refresh_today_messages(42)
        self.assertNotIn("Математика</b>", self.bot.edit_message_text.call_args.kwargs["text"])
        await self.db.toggle_highlight_current(42)
        await self.service.refresh_today_messages(42)
        self.assertIn("Математика</b>", self.bot.edit_message_text.call_args.kwargs["text"])

    async def test_restart_keeps_snapshot_and_midnight_cleans_it(self) -> None:
        await self.send()
        await self.db.close()
        self.db = Database(Path(self.directory.name) / "test.sqlite3")
        await self.db.initialize()
        self.service = ScheduleService(self.bot, self.db, SimpleNamespace(),
                                       self.now.tzinfo, self.reporter)
        await self.db.upsert_user(42, "Б", 2, "Иван", None)
        self.advance(10)
        await self.service.refresh_today_messages()
        self.assertIn("Физика</b>", self.bot.edit_message_text.call_args.kwargs["text"])
        self.clock.now.return_value = self.now + timedelta(days=1)
        await self.service.refresh_today_messages()
        self.assertNotIn("Физика</b>", self.bot.edit_message_text.call_args.kwargs["text"])
        self.assertEqual(await self.db.today_messages(42), [])

    async def test_deleted_message_is_forgotten(self) -> None:
        await self.send()
        self.bot.edit_message_text.side_effect = TelegramBadRequest(
            method=EditMessageText(text="x"), message="message to edit not found")
        self.advance(10)
        await self.service.refresh_today_messages()
        self.assertEqual(await self.db.today_messages(42), [])
        self.reporter.report.assert_not_awaited()

    async def test_failed_cleanup_retries_without_losing_latest(self) -> None:
        await self.send()
        self.bot.edit_message_text.side_effect = TelegramNetworkError(
            method=EditMessageText(text="x"), message="Temporary error")
        await self.send()
        self.assertEqual(len(await self.db.today_messages(42)), 2)
        self.bot.edit_message_text.side_effect = None
        await self.service.refresh_today_messages()
        self.assertEqual([r["message_id"] for r in await self.db.today_messages(42)], [2])

    async def test_failed_send_keeps_old_message_active(self) -> None:
        await self.send()
        self.message.answer.side_effect = RuntimeError("Send failed")
        with self.assertRaises(RuntimeError):
            await self.send()
        records = await self.db.today_messages(42)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["is_latest"], 1)
        self.bot.edit_message_text.assert_not_awaited()

    async def test_concurrent_requests_and_tick_keep_latest(self) -> None:
        await asyncio.gather(self.send(), self.send(), self.service.refresh_today_messages(42))
        self.assertEqual([r["message_id"] for r in await self.db.today_messages(42)], [2])


if __name__ == "__main__":
    unittest.main()
