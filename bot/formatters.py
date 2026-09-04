import html
from datetime import date

from bot.models import DaySchedule

MONTHS = (
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
WEEKDAYS = (
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье",
)


def human_date(day: date) -> str:
    return f"{day.day} {MONTHS[day.month]}, {WEEKDAYS[day.weekday()]}"


def format_schedule(schedule: DaySchedule, title: str | None = None) -> str:
    heading = title or "Расписание"
    lines = [f"<b>{html.escape(heading)}</b>", f"<i>{human_date(schedule.day)}</i>", ""]
    if not schedule.lessons:
        lines.append("🌿 Пар нет — можно выдохнуть!")
        return "\n".join(lines)

    for index, lesson in enumerate(schedule.lessons, start=1):
        time_range = f"{lesson.starts_at:%H:%M}–{lesson.ends_at:%H:%M}"
        lines.append(f"<b>{index}. {time_range}</b>  {html.escape(lesson.subject)}")
        details: list[str] = []
        if lesson.room:
            details.append(f"📍 {html.escape(lesson.room)}")
        if lesson.teacher:
            details.append(f"👤 {html.escape(lesson.teacher)}")
        if details:
            lines.append(" · ".join(details))
        lines.append("")
    lines.append(f"Всего пар: <b>{len(schedule.lessons)}</b>")
    return "\n".join(lines)


def format_change(schedule: DaySchedule) -> str:
    return "🔔 <b>Расписание изменилось</b>\n\n" + format_schedule(schedule, "Актуальная версия")

