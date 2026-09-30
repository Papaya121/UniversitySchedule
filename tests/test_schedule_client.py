import unittest
from datetime import date

from bot.schedule_client import parse_schedule_html
from bot.formatters import format_schedule


HTML = """
<div class="table">
  <div>
    <div><strong>04 сентября 2026</strong></div>
    <div>пятница</div>
    <table>
      <tr>
        <td rowspan="1">13:40-15:10</td>
        <td>лаб. Архитектура информационных систем<br/>1 п.г.<br/><br/>
            ИС2-261-ОБ<br/><br/><a href="/map">3Комп/АГор</a><br/>Мальцев В.В.<br/></td>
      </tr>
      <tr>
        <td rowspan="2">15:20-16:50</td>
        <td>Лек. История России<br/><br/>ИС2-261-ОБ<br/>ИС2-262-ОБ<br/>ИС2-263-ОБ<br/><br/>
            <a href="/map">314Л/Гл</a><br/>Разиньков М.Е.<br/></td>
      </tr>
      <tr>
        <td>лаб. Программирование<br/>2 п.г.<br/><br/>ИС2-261-ОБ<br/><br/>
            <a href="/map">108Комп/7к</a><br/>Оксюта О.В.<br/></td>
      </tr>
    </table>
  </div>
  <div><div><strong>05 сентября 2026</strong></div><table><tr><td>Нет пар.</td></tr></table></div>
</div>
"""


class ParseScheduleTest(unittest.TestCase):
    def test_parses_times_details_and_subgroups(self) -> None:
        schedules = parse_schedule_html(HTML)
        schedule = schedules[date(2026, 9, 4)]

        self.assertEqual(len(schedule.lessons), 3)
        self.assertEqual(schedule.lessons[0].subgroup, 1)
        self.assertEqual(schedule.lessons[0].room, "3Комп/АГор")
        self.assertEqual(schedule.lessons[1].subgroup, None)
        self.assertEqual(
            schedule.lessons[1].groups,
            ("ИС2-261-ОБ", "ИС2-262-ОБ", "ИС2-263-ОБ"),
        )
        self.assertEqual(schedule.lessons[2].subgroup, 2)
        self.assertEqual(schedule.lessons[2].starts_at.hour, 15)

    def test_filters_subgroups_but_keeps_lectures(self) -> None:
        schedule = parse_schedule_html(HTML)[date(2026, 9, 4)]
        first = schedule.for_subgroup(1)
        second = schedule.for_subgroup(2)

        self.assertEqual([x.subgroup for x in first.lessons], [1, None])
        self.assertEqual([x.subgroup for x in second.lessons], [None, 2])

    def test_parses_empty_day(self) -> None:
        schedules = parse_schedule_html(HTML)
        self.assertEqual(schedules[date(2026, 9, 5)].lessons, ())

    def test_displays_groups_only_for_shared_lesson(self) -> None:
        schedule = parse_schedule_html(HTML)[date(2026, 9, 4)]
        text = format_schedule(schedule.for_subgroup(1))
        self.assertIn("👥 ИС2-261-ОБ, ИС2-262-ОБ, ИС2-263-ОБ", text)
        self.assertEqual(text.count("👥"), 1)


if __name__ == "__main__":
    unittest.main()
