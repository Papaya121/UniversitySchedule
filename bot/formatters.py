import html
from datetime import date, datetime, timedelta

from bot.models import DaySchedule, Lesson

MONTHS = (
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
WEEKDAYS = (
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье",
)


def human_date(day: date) -> str:
    return f"{day.day} {MONTHS[day.month]}, {WEEKDAYS[day.weekday()]}"


def format_shared_groups(lesson: Lesson, show_shared_groups: bool = True) -> str:
    if not show_shared_groups or len(lesson.groups) <= 1:
        return ""
    return "👥 Группы:\n" + "\n".join(
        f"• {html.escape(group)}" for group in lesson.groups
    )


def format_schedule(
    schedule: DaySchedule, title: str | None = None,
    show_shared_groups: bool = True,
    *, now: datetime | None = None, highlight_current: bool = True,
    weekly: bool = False,
) -> str:
    highlight_day = bool(
        highlight_current and weekly and now and schedule.day == now.date()
    )
    highlighted = set()
    if highlight_current and not weekly and now and schedule.day == now.date():
        current_time = now.time()
        highlighted = {
            index for index, lesson in enumerate(schedule.lessons)
            if lesson.starts_at <= current_time < lesson.ends_at
        }
        if not highlighted:
            upcoming = [
                lesson.starts_at for lesson in schedule.lessons
                if lesson.starts_at > current_time
            ]
            if upcoming:
                next_start = min(upcoming)
                first_start = min(lesson.starts_at for lesson in schedule.lessons)
                until_next = datetime.combine(schedule.day, next_start, tzinfo=now.tzinfo) - now
                if next_start != first_start or until_next <= timedelta(minutes=30):
                    highlighted = {
                        index for index, lesson in enumerate(schedule.lessons)
                        if lesson.starts_at == next_start
                    }

    def finish() -> str:
        text = "\n".join(lines)
        if highlight_day:
            return "<b>" + text.replace("<b>", "").replace("</b>", "") + "</b>"
        return text

    heading = title or "Расписание"
    lines = [f"<b>{html.escape(heading)}</b>", f"<i>{human_date(schedule.day)}</i>", ""]
    if not schedule.lessons:
        lines.append("🌿 Пар нет — можно выдохнуть!")
        return finish()

    for index, lesson in enumerate(schedule.lessons, start=1):
        block_start = len(lines)
        time_range = f"{lesson.starts_at:%H:%M}–{lesson.ends_at:%H:%M}"
        lines.append(f"<b>{index}. {time_range}</b>  {html.escape(lesson.subject)}")
        group_text = format_shared_groups(lesson, show_shared_groups)
        if group_text:
            lines.append(group_text)
        details: list[str] = []
        if lesson.room:
            details.append(f"📍 {html.escape(lesson.room)}")
        if lesson.teacher:
            details.append(f"👤 {html.escape(lesson.teacher)}")
        if details:
            lines.append(" · ".join(details))
        if index - 1 in highlighted:
            block = "\n".join(lines[block_start:])
            lines[block_start:] = [
                "<b>" + block.replace("<b>", "").replace("</b>", "") + "</b>"
            ]
        lines.append("")
    lines.append(f"Всего пар: <b>{len(schedule.lessons)}</b>")
    return finish()


def format_change(schedule: DaySchedule, show_shared_groups: bool = True) -> str:
    return "🔔 <b>Расписание изменилось</b>\n\n" + format_schedule(
        schedule, "Актуальная версия", show_shared_groups
    )


def format_new_schedule_period(days: list[date]) -> str:
    ordered = sorted(set(days))
    first = ordered[0]
    last = ordered[-1]
    if first == last:
        period = f"{first.day} {MONTHS[first.month]}"
    elif first.month == last.month and first.year == last.year:
        period = f"{first.day}–{last.day} {MONTHS[first.month]}"
    else:
        period = (
            f"{first.day} {MONTHS[first.month]} – "
            f"{last.day} {MONTHS[last.month]}"
        )
    return (
        "📚 <b>Добавлено новое расписание</b>\n\n"
        f"Появились пары на период <b>{period}</b>.\n"
        "Открой раздел «🗓 Неделя», чтобы посмотреть подробности."
    )
