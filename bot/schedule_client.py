import asyncio
import logging
import re
from datetime import date, time, timedelta

import httpx
from bs4 import BeautifulSoup, Tag

from bot.models import DaySchedule, Lesson

logger = logging.getLogger(__name__)

DATE_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})$")
SUBGROUP_RE = re.compile(r"\b([12])\s*п\.?\s*г\.? ?", re.IGNORECASE)
GROUP_RE = re.compile(r"^[А-ЯA-Z]{1,4}\d+-\d+-[А-ЯA-Z]{1,4}$", re.IGNORECASE)
DATE_RE = re.compile(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", re.IGNORECASE)


def _parse_date(text: str) -> date | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    month = DATE_MONTHS.get(match.group(2).lower())
    if not month:
        return None
    return date(int(match.group(3)), month, int(match.group(1)))


def _clean(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def parse_schedule_html(html: str) -> dict[date, DaySchedule]:
    soup = BeautifulSoup(html, "html.parser")
    result: dict[date, DaySchedule] = {}

    for block in soup.select("div.table > div"):
        strong = block.find("strong")
        table = block.find("table")
        if not strong or not table:
            continue
        day = _parse_date(strong.get_text(" ", strip=True))
        if not day:
            continue

        lessons: list[Lesson] = []
        current_time: tuple[time, time] | None = None
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if not cells:
                continue
            first_text = _clean(cells[0].get_text(" ", strip=True))
            match = TIME_RE.match(first_text)
            content: Tag
            if match and len(cells) >= 2:
                current_time = (
                    time(int(match.group(1)), int(match.group(2))),
                    time(int(match.group(3)), int(match.group(4))),
                )
                content = cells[-1]
            elif current_time:
                content = cells[-1]
            else:
                continue

            parts = [_clean(x) for x in content.stripped_strings if _clean(x)]
            if not parts or parts[0].lower().startswith("нет пар"):
                continue
            subject = parts[0]
            subgroup: int | None = None
            subject_subgroup = SUBGROUP_RE.search(" ".join(parts[:2]))
            if subject_subgroup:
                subgroup = int(subject_subgroup.group(1))

            details = [
                p for p in parts[1:]
                if not SUBGROUP_RE.search(p) and not GROUP_RE.match(p)
            ]
            room = details[-2] if len(details) >= 2 else None
            teacher = details[-1] if details else None
            lessons.append(Lesson(*current_time, subject, subgroup, room, teacher))

        result[day] = DaySchedule(day, tuple(lessons))
    return result


class ScheduleClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(20),
            follow_redirects=True,
            headers={"User-Agent": "VGLTU-Schedule-Telegram-Bot/1.0"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self, group: str, start: date, days: int = 14) -> dict[date, DaySchedule]:
        schedules: dict[date, DaySchedule] = {}
        end = start + timedelta(days=days)
        query_day = start
        # Обычно сайт сразу возвращает 14 дней. Цикл также переживёт изменение
        # размера выдаваемого сайтом окна, не создавая лишних запросов.
        for _ in range(4):
            response = await self._request_with_retry(group, query_day)
            page = parse_schedule_html(response.text)
            schedules.update(page)
            if page and max(page) >= end - timedelta(days=1):
                break
            query_day = max(page, default=query_day + timedelta(days=6)) + timedelta(days=1)
        return {day: value for day, value in schedules.items() if start <= day < end}

    async def group_exists(self, group: str, day: date) -> bool:
        academic_year = day.year if day.month >= 9 else day.year - 1
        probe_days = (
            day,
            day - timedelta(days=28),
            day + timedelta(days=28),
            date(academic_year, 9, 1),
            date(academic_year + 1, 2, 1),
        )
        # For an unknown group the site returns a normal-looking calendar where
        # every day says "Нет пар". Confirm existence by finding at least one
        # actual lesson in the current academic year.
        checked: set[date] = set()
        for probe_day in probe_days:
            if probe_day in checked:
                continue
            checked.add(probe_day)
            response = await self._request_with_retry(group, probe_day)
            schedules = parse_schedule_html(response.text)
            if any(schedule.lessons for schedule in schedules.values()):
                return True
        return False

    async def _request_with_retry(self, group: str, day: date) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.get(
                    self.base_url,
                    params={"group": group, "date": day.isoformat()},
                )
                response.raise_for_status()
                return response
            except (httpx.HTTPError, asyncio.TimeoutError) as error:
                last_error = error
                logger.warning("Schedule request failed (attempt %s): %s", attempt + 1, error)
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
        assert last_error is not None
        raise last_error
