import unittest
from unittest.mock import patch
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.database import Database
from bot.models import DaySchedule, Lesson
from bot.service import ScheduleService, morning_delivery_time


TZ = ZoneInfo("Europe/Moscow")
DAY = date(2026, 9, 22)


def lesson(starts_at: time, ends_at: time) -> Lesson:
    return Lesson(starts_at, ends_at, "Предмет", None)


class MorningDeliveryTimeTest(unittest.TestCase):
    def test_sends_at_nine_for_late_first_lesson(self) -> None:
        schedule = DaySchedule(DAY, (lesson(time(13, 40), time(15, 10)),))
        result = morning_delivery_time(schedule, time(9, 0), TZ)
        self.assertEqual(result.time(), time(9, 0))


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))


class FakeClient:
    def __init__(self, schedules: dict[date, DaySchedule]) -> None:
        self.schedules = schedules

    async def fetch(
        self, group_name: str, start: date, days: int = 14
    ) -> dict[date, DaySchedule]:
        return self.schedules


class FakeReporter:
    def __init__(self) -> None:
        self.notifications: list[str] = []
        self.errors: list[tuple[str, BaseException]] = []

    async def notify_admins(self, text: str) -> bool:
        self.notifications.append(text)
        return True

    async def report(self, context: str, error: BaseException) -> None:
        self.errors.append((context, error))


class ScheduleProtectionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.sqlite3")
        await self.db.initialize()
        self.bot = FakeBot()
        self.reporter = FakeReporter()
        self.today = datetime.now(TZ).date()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.temp_dir.cleanup()

    def make_service(self, schedules: dict[date, DaySchedule]) -> ScheduleService:
        return ScheduleService(
            self.bot, self.db, FakeClient(schedules), TZ, self.reporter
        )

    async def test_lesson_start_only_for_enabled_subgroup_once(self) -> None:
        group = "ИС2-261-ОБ"
        fixed_now = datetime(2026, 9, 30, 10, 0, tzinfo=TZ)
        await self.db.upsert_user(42, group, 1, "Иван", None)
        await self.db.upsert_user(43, group, 1, "Анна", None)
        await self.db.upsert_user(44, group, 2, "Пётр", None)
        await self.db.toggle_lesson_start_notifications(42)
        await self.db.toggle_lesson_start_notifications(44)
        schedule = DaySchedule(fixed_now.date(), (
            Lesson(time(10), time(11, 30), "Лабораторная", 1, "101", "Иванов И.И."),
            Lesson(time(11, 40), time(13, 10), "Другая пара", None),
        ))
        service = self.make_service({fixed_now.date(): schedule})

        with patch("bot.service.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            await service.send_lesson_start()
            await service.send_lesson_start()

        self.assertEqual(len(self.bot.messages), 1)
        self.assertEqual(self.bot.messages[0][0], 42)
        self.assertIn("Пара началась", self.bot.messages[0][1])
        self.assertIn("Лабораторная", self.bot.messages[0][1])

    async def test_daily_toggle_controls_morning_and_end_of_day(self) -> None:
        group = "ИС2-261-ОБ"
        fixed_now = datetime(2026, 9, 30, 21, 0, tzinfo=TZ)
        await self.db.upsert_user(42, group, 1, "Иван", None)
        await self.db.upsert_user(43, group, 1, "Анна", None)
        await self.db.toggle_daily_schedule_notifications(43)
        schedules = {
            fixed_now.date(): DaySchedule(fixed_now.date(), ()),
            fixed_now.date() + timedelta(days=1): DaySchedule(
                fixed_now.date() + timedelta(days=1), ()
            ),
        }
        service = self.make_service(schedules)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now

        with patch("bot.service.datetime", FrozenDateTime):
            await service.send_morning()
            await service.send_tomorrow_after_last_lesson()

        self.assertEqual([chat_id for chat_id, _ in self.bot.messages], [42, 42])
        self.assertIn("Доброе утро", self.bot.messages[0][1])
        self.assertIn("Учебный день закончен", self.bot.messages[1][1])

    async def test_rejects_all_empty_response_and_keeps_cached_schedule(self) -> None:
        group = "ИС2-261-ОБ"
        old = DaySchedule(
            self.today, (lesson(time(8, 0), time(9, 30)),)
        )
        await self.db.save_snapshot(group, self.today, 1, "old", 1)
        await self.db.save_schedule_cache(group, {self.today: old})
        empty = {
            self.today + timedelta(days=offset): DaySchedule(
                self.today + timedelta(days=offset), ()
            )
            for offset in range(14)
        }
        service = self.make_service(empty)

        result = await service.schedules(group, force=True)
        again = await service.schedules(group, force=True)

        self.assertEqual(result[self.today], old)
        self.assertEqual(again[self.today], old)
        self.assertEqual(len(self.reporter.notifications), 1)
        self.assertIn(group, self.reporter.notifications[0])

    async def test_notifies_when_schedule_recovers(self) -> None:
        group = "ИС2-261-ОБ"
        await self.db.save_snapshot(group, self.today, 1, "old", 1)
        await self.db.start_empty_schedule_incident(group)
        recovered = DaySchedule(
            self.today, (lesson(time(8, 0), time(9, 30)),)
        )
        service = self.make_service({self.today: recovered})

        result = await service.schedules(group, force=True)

        self.assertEqual(result[self.today], recovered)
        self.assertEqual(len(self.reporter.notifications), 1)
        self.assertIn("снова доступно", self.reporter.notifications[0])

    async def test_groups_new_days_into_one_user_notification(self) -> None:
        group = "ИС2-261-ОБ"
        await self.db.upsert_user(42, group, 1, "Иван", "ivan")
        schedules: dict[date, DaySchedule] = {}
        empty_fingerprint = ScheduleService.fingerprint(DaySchedule(self.today, ()))
        for offset in range(3):
            day = self.today + timedelta(days=offset)
            schedules[day] = DaySchedule(
                day, (lesson(time(8, 0), time(9, 30)),)
            )
            for subgroup in (1, 2):
                await self.db.save_snapshot(
                    group, day, subgroup, empty_fingerprint, 0
                )
        service = self.make_service(schedules)

        await service.check_changes()

        self.assertEqual(len(self.bot.messages), 1)
        self.assertIn("Добавлено новое расписание", self.bot.messages[0][1])
        self.assertNotIn("Расписание изменилось", self.bot.messages[0][1])

    async def test_existing_day_change_keeps_detailed_notification(self) -> None:
        group = "ИС2-261-ОБ"
        await self.db.upsert_user(42, group, 1, "Иван", "ivan")
        old_schedule = DaySchedule(
            self.today,
            (Lesson(time(8, 0), time(9, 30), "Старый предмет", None),),
        )
        new_schedule = DaySchedule(
            self.today,
            (Lesson(time(8, 0), time(9, 30), "Новый предмет", None),),
        )
        for subgroup in (1, 2):
            await self.db.save_snapshot(
                group,
                self.today,
                subgroup,
                ScheduleService.fingerprint(old_schedule),
                1,
            )
        service = self.make_service({self.today: new_schedule})

        await service.check_changes()

        self.assertEqual(len(self.bot.messages), 1)
        self.assertIn("Расписание изменилось", self.bot.messages[0][1])
        self.assertIn("Новый предмет", self.bot.messages[0][1])


class MorningDeliveryAdditionalTest(unittest.TestCase):
    def test_sends_ninety_minutes_before_early_lesson(self) -> None:
        schedule = DaySchedule(DAY, (lesson(time(8, 0), time(9, 30)),))
        result = morning_delivery_time(schedule, time(9, 0), TZ)
        self.assertEqual(result.time(), time(6, 30))

    def test_uses_earliest_lesson_even_if_input_is_unsorted(self) -> None:
        schedule = DaySchedule(DAY, (
            lesson(time(10, 30), time(12, 0)),
            lesson(time(9, 20), time(10, 50)),
        ))
        result = morning_delivery_time(schedule, time(9, 0), TZ)
        self.assertEqual(result.time(), time(7, 50))

    def test_sends_at_nine_when_there_are_no_lessons(self) -> None:
        result = morning_delivery_time(DaySchedule(DAY, ()), time(9, 0), TZ)
        self.assertEqual(result.time(), time(9, 0))


if __name__ == "__main__":
    unittest.main()
