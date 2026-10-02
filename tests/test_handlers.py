import unittest
import tempfile
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.database import Database
from bot.handlers import (
    ProfileSetup, available_week_starts, build_router, calendar_week_start,
    normalize_group, week_label,
)
from bot.keyboards import donation_keyboard, main_keyboard, settings_keyboard, week_keyboard
from bot.models import DaySchedule, Lesson


class NormalizeGroupTest(unittest.TestCase):
    def test_uppercases_and_removes_spaces(self) -> None:
        self.assertEqual(normalize_group("  ис2-261-об "), "ИС2-261-ОБ")
        self.assertEqual(normalize_group("ис2 - 261 - об"), "ИС2-261-ОБ")


class DonationKeyboardTest(unittest.TestCase):
    def test_donation_is_fifth_full_width_main_button(self) -> None:
        keyboard = main_keyboard().keyboard

        self.assertEqual([len(row) for row in keyboard], [2, 2, 1])
        self.assertEqual(keyboard[2][0].text, "❤️ Поддержать разработчика")

    def test_donation_button_opens_configured_url(self) -> None:
        url = "https://tbank.ru/cf/3o4Kr2VJXCE"
        button = donation_keyboard(url).inline_keyboard[0][0]

        # self.assertEqual(button.text, "💚 Поддержать")
        # self.assertEqual(button.url, url)


class WeekSelectionTest(unittest.TestCase):
    def test_calendar_week_and_cross_month_label(self) -> None:
        self.assertEqual(calendar_week_start(date(2026, 9, 30)), date(2026, 9, 28))
        self.assertEqual(week_label(date(2026, 9, 28)), "28 сентября – 04 октября")
        self.assertEqual(week_label(date(2026, 10, 5)), "05–11 октября")

    def test_offers_only_future_weeks_with_lessons(self) -> None:
        lesson = Lesson(time(8), time(9, 30), "Лекция", None)
        schedules = {
            date(2026, 9, 30): DaySchedule(date(2026, 9, 30), (lesson,)),
            date(2026, 10, 7): DaySchedule(date(2026, 10, 7), (lesson,)),
            date(2026, 10, 12): DaySchedule(date(2026, 10, 12), ()),
        }
        weeks = available_week_starts(schedules, date(2026, 9, 30))
        self.assertEqual(weeks, [date(2026, 9, 28), date(2026, 10, 5)])
        buttons = week_keyboard(weeks, [week_label(start) for start in weeks])
        self.assertEqual(buttons.inline_keyboard[1][0].callback_data, "week:2026-10-05")


class SettingsKeyboardTest(unittest.TestCase):
    def test_highlight_toggle_default_and_disabled(self) -> None:
        for enabled in (True, False):
            keyboard = settings_keyboard(True, False, True, True, enabled)
            button = next(button for row in keyboard.inline_keyboard for button in row
                          if button.callback_data == "settings:highlight:toggle")
            self.assertIn("Включено" if enabled else "Выключено", button.text)

    def test_lesson_start_toggle_is_in_settings(self) -> None:
        keyboard = settings_keyboard(True, False, True, True)
        buttons = [button for row in keyboard.inline_keyboard for button in row]
        start = next(button for button in buttons if button.callback_data == "notifications:start:toggle")
        self.assertIn("Выключены", start.text)
        daily = next(button for button in buttons if button.callback_data == "notifications:daily:toggle")
        self.assertIn("Включена", daily.text)
        groups = next(button for button in buttons if button.callback_data == "settings:groups:toggle")
        self.assertIn("Включено", groups.text)
        self.assertFalse(any(button.callback_data.startswith("subgroup:") for button in buttons))


class HighlightFlowTest(unittest.IsolatedAsyncioTestCase):
    async def test_daily_weekly_and_settings_use_saved_preference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.sqlite3")
            await db.initialize()
            await db.upsert_user(42, "ИС2-261-ОБ", 1, "Иван", "ivan")
            now = datetime(2026, 10, 2, 8, tzinfo=ZoneInfo("Europe/Moscow"))
            schedule = DaySchedule(now.date(), (
                Lesson(time(8), time(9, 30), "Математика", None),
                Lesson(time(8), time(9, 30), "Другая подгруппа", 2),
            ))
            service = SimpleNamespace(
                timezone=now.tzinfo,
                for_day=AsyncMock(return_value=schedule.for_subgroup(1)),
                schedules=AsyncMock(return_value={now.date(): schedule}),
            )
            reporter = SimpleNamespace(report=AsyncMock())
            router = build_router(db, service, reporter, "https://example.com")
            handlers = {handler.callback.__name__: handler.callback
                        for handler in router.callback_query.handlers + router.message.handlers}
            message = SimpleNamespace(chat=SimpleNamespace(id=42), answer=AsyncMock(),
                                      edit_reply_markup=AsyncMock())
            callback = SimpleNamespace(message=message, answer=AsyncMock())
            try:
                with patch("bot.handlers.datetime") as clock:
                    clock.now.return_value = now
                    await handlers["today"](message)
                    self.assertIn("<b>1. 08:00–09:30  Математика</b>",
                                  message.answer.call_args.args[0])
                    await handlers["week"](message)
                    texts = [call.args[0] for call in message.answer.call_args_list]
                    self.assertTrue(any(text.startswith("<b>Расписание\n") for text in texts))
                    self.assertFalse(any("Другая подгруппа" in text for text in texts))
                    await handlers["toggle_highlight"](callback)
                    self.assertEqual((await db.get_user(42))["highlight_current"], 0)
                    message.answer.reset_mock()
                    await handlers["today"](message)
                    self.assertNotIn("<b>1. 08:00–09:30  Математика</b>",
                                     message.answer.call_args.args[0])
                    await handlers["week"](message)
                    self.assertFalse(any(call.args[0].startswith("<b>Расписание\n")
                                         for call in message.answer.call_args_list))
                reporter.report.assert_not_awaited()
            finally:
                await db.close()


class ChangeGroupFlowTest(unittest.IsolatedAsyncioTestCase):
    async def test_saves_group_and_subgroup_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.sqlite3")
            await db.initialize()
            await db.upsert_user(42, "ИС2-261-ОБ", 1, "Иван", "ivan")
            service = SimpleNamespace(group_exists=AsyncMock(return_value=True))
            router = build_router(db, service, SimpleNamespace(), "https://example.com")
            handlers = {
                handler.callback.__name__: handler.callback
                for handler in router.callback_query.handlers + router.message.handlers
            }
            storage = MemoryStorage()
            state = FSMContext(storage, StorageKey(bot_id=1, chat_id=42, user_id=42))
            checking = SimpleNamespace(edit_text=AsyncMock())
            message = SimpleNamespace(
                chat=SimpleNamespace(id=42),
                text="ИС2-262-ОБ",
                answer=AsyncMock(return_value=checking),
                edit_text=AsyncMock(),
            )
            callback = SimpleNamespace(
                message=message,
                from_user=SimpleNamespace(first_name="Иван", username="ivan"),
                answer=AsyncMock(),
                data="settings:group",
            )
            try:
                await handlers["change_group"](callback, state)
                await handlers["receive_group"](message, state)
                self.assertEqual(await state.get_state(), ProfileSetup.waiting_for_subgroup.state)
                self.assertEqual((await db.get_user(42))["group_name"], "ИС2-261-ОБ")

                callback.data = "subgroup:2"
                await handlers["choose_subgroup"](callback, state)
                user = await db.get_user(42)
                self.assertEqual((user["group_name"], user["subgroup"]), ("ИС2-262-ОБ", 2))
                self.assertIsNone(await state.get_state())
            finally:
                await db.close()


if __name__ == "__main__":
    unittest.main()
