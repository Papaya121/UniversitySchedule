import unittest
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from bot.formatters import format_schedule
from bot.models import DaySchedule, Lesson


class ScheduleHighlightTest(unittest.TestCase):
    def setUp(self) -> None:
        self.day = date(2026, 10, 2)
        self.schedule = DaySchedule(self.day, (
            Lesson(time(8), time(9, 30), "Математика & логика", None,
                   "Ауд. <1>", "Иванов", ("А", "Б")),
            Lesson(time(10), time(11, 30), "Физика", None),
        ))

    def render(self, hour: int, minute: int = 0, second: int = 0, **kwargs) -> str:
        now = datetime(2026, 10, 2, hour, minute, second,
                       tzinfo=ZoneInfo("Europe/Moscow"))
        return format_schedule(self.schedule, now=now, **kwargs)

    def test_current_and_next_lesson_boundaries(self) -> None:
        for clock, subject in (
            ((7, 29, 59), None), ((7, 30, 0), "Математика &amp; логика"),
            ((8, 0, 0), "Математика &amp; логика"),
            ((9, 29, 59), "Математика &amp; логика"),
            ((9, 30, 0), "Физика"), ((10, 0, 0), "Физика"),
            ((11, 30, 0), None), ((23, 0, 0), None),
        ):
            with self.subTest(clock=clock):
                text = self.render(*clock)
                self.assertEqual("<b>1. 08:00–09:30  Математика" in text,
                                 subject == "Математика &amp; логика")
                self.assertEqual("<b>2. 10:00–11:30  Физика</b>" in text,
                                 subject == "Физика")

    def test_current_lesson_includes_details_and_escapes_html(self) -> None:
        text = self.render(8)
        self.assertIn("Математика &amp; логика\n👥 Группы:", text)
        self.assertIn("📍 Ауд. &lt;1&gt; · 👤 Иванов</b>", text)
        self.assertNotIn("<b><b>", text)

    def test_disabled_and_other_dates_have_no_lesson_highlight(self) -> None:
        self.assertNotIn("<b>1. 08:00–09:30  Математика", self.render(8, highlight_current=False))
        for delta in (-1, 1):
            now = datetime(2026, 10, 2, 8) + timedelta(days=delta)
            self.assertNotIn("<b>1. 08:00–09:30  Математика",
                             format_schedule(self.schedule, now=now))

    def test_week_highlights_entire_current_day_even_without_lessons(self) -> None:
        for schedule in (self.schedule, DaySchedule(self.day, ())):
            now = datetime(2026, 10, 2, 23)
            text = format_schedule(schedule, now=now, weekly=True)
            self.assertTrue(text.startswith("<b>Расписание\n"))
            self.assertTrue(text.endswith("</b>"))
            self.assertEqual(text.count("<b>"), 1)
            for options in ({"highlight_current": False}, {"now": now + timedelta(days=1)}):
                kwargs = {"now": now, "weekly": True, **options}
                self.assertFalse(format_schedule(schedule, **kwargs).startswith("<b>Расписание\n"))

    def test_empty_day_and_long_break(self) -> None:
        now = datetime(2026, 10, 2, 12)
        self.assertIn("Пар нет", format_schedule(DaySchedule(self.day, ()), now=now))
        schedule = DaySchedule(self.day, (
            self.schedule.lessons[0], Lesson(time(15), time(16, 30), "Физика", None),
        ))
        self.assertIn("<b>2. 15:00–16:30  Физика</b>", format_schedule(schedule, now=now))


if __name__ == "__main__":
    unittest.main()
