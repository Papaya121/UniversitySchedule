import asyncio
import hashlib
import html
import json
import logging
from datetime import date, datetime, time, timedelta

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

from bot.database import Database
from bot.error_reporter import ErrorReporter
from bot.formatters import format_change, format_new_schedule_period, format_schedule
from bot.models import DaySchedule
from bot.schedule_client import ScheduleClient

logger = logging.getLogger(__name__)


class SuspiciousEmptyScheduleError(RuntimeError):
    """The website returned an all-empty window that was rejected by the guard."""


def morning_delivery_time(
    schedule: DaySchedule, default_time: time, timezone
) -> datetime:
    """Return min(default time, 90 minutes before the first lesson)."""
    default_at = datetime.combine(schedule.day, default_time, tzinfo=timezone)
    if not schedule.lessons:
        return default_at
    first_start = min(lesson.starts_at for lesson in schedule.lessons)
    before_first = datetime.combine(schedule.day, first_start, tzinfo=timezone) - timedelta(
        minutes=90
    )
    return min(default_at, before_first)


class ScheduleService:
    def __init__(
        self, bot: Bot, database: Database, client: ScheduleClient, timezone,
        error_reporter: ErrorReporter, morning_time: time = time(9, 0),
    ) -> None:
        self.bot = bot
        self.db = database
        self.client = client
        self.timezone = timezone
        self.error_reporter = error_reporter
        self.morning_time = morning_time
        self._cache: dict[str, dict[date, DaySchedule]] = {}
        self._cache_at: dict[str, datetime] = {}
        self._fetch_lock = asyncio.Lock()

    async def schedules(
        self, group_name: str, force: bool = False, allow_stale: bool = False
    ) -> dict[date, DaySchedule]:
        now = datetime.now(self.timezone)
        if allow_stale and group_name in self._cache:
            return self._cache[group_name]
        cached_at = self._cache_at.get(group_name)
        if not force and cached_at and now - cached_at < timedelta(minutes=5):
            return self._cache[group_name]
        async with self._fetch_lock:
            now = datetime.now(self.timezone)
            cached_at = self._cache_at.get(group_name)
            if not force and cached_at and now - cached_at < timedelta(minutes=5):
                return self._cache[group_name]
            candidate = await self.client.fetch(group_name, now.date(), days=14)
            candidate_days = sorted(candidate)
            range_start = candidate_days[0] if candidate_days else now.date()
            range_end = candidate_days[-1] if candidate_days else now.date() + timedelta(days=13)
            all_days_empty = not candidate or all(
                not schedule.lessons for schedule in candidate.values()
            )
            previously_had_lessons = await self.db.had_lessons_between(
                group_name, range_start, range_end
            )
            if all_days_empty and previously_had_lessons:
                first_alert = await self.db.start_empty_schedule_incident(group_name)
                if first_alert:
                    alert_delivered = await self.error_reporter.notify_admins(
                        "🛡 <b>Защита расписания сработала</b>\n\n"
                        f"Группа: <b>{html.escape(group_name)}</b>\n"
                        f"Период: <b>{range_start:%d.%m.%Y}–{range_end:%d.%m.%Y}</b>\n\n"
                        "Сайт ВГЛТУ вернул только дни без пар, хотя раньше на этих "
                        "датах были занятия. Обновление отклонено, сохранена последняя "
                        "корректная версия."
                    )
                    if not alert_delivered:
                        await self.db.release_empty_schedule_incident(group_name)
                cached = self._cache.get(group_name)
                if cached is None:
                    cached = await self.db.load_schedule_cache(group_name)
                if cached:
                    self._cache[group_name] = cached
                    self._cache_at[group_name] = now
                    return cached
                raise SuspiciousEmptyScheduleError(
                    f"All-empty schedule rejected for {group_name}"
                )

            recovered = await self.db.resolve_empty_schedule_incident(group_name)
            if recovered:
                await self.error_reporter.notify_admins(
                    "✅ <b>Расписание снова доступно</b>\n\n"
                    f"Группа <b>{html.escape(group_name)}</b>: сайт вернул корректные "
                    "данные, автоматические обновления возобновлены."
                )
            await self.db.save_schedule_cache(group_name, candidate)
            self._cache[group_name] = candidate
            self._cache_at[group_name] = now
            return self._cache[group_name]

    async def for_day(
        self, group_name: str, day: date, subgroup: int, force: bool = False,
        allow_stale: bool = False,
    ) -> DaySchedule:
        schedules = await self.schedules(
            group_name, force=force, allow_stale=allow_stale
        )
        return schedules.get(day, DaySchedule(day, ())).for_subgroup(subgroup)

    async def group_exists(self, group_name: str) -> bool:
        return await self.client.group_exists(group_name, datetime.now(self.timezone).date())

    @staticmethod
    def fingerprint(schedule: DaySchedule) -> str:
        value = json.dumps(
            [lesson.fingerprint() for lesson in schedule.lessons],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(value.encode()).hexdigest()

    async def check_changes(self) -> None:
        """Compare a 14-day window and notify only affected subgroups."""
        try:
            now = datetime.now(self.timezone)
            for group_name in await self.db.active_groups():
                try:
                    schedules = await self.schedules(group_name, force=True)
                except SuspiciousEmptyScheduleError:
                    continue
                except Exception as error:
                    await self.error_reporter.report(
                        f"Проверка изменений расписания группы {group_name}", error
                    )
                    continue
                established_schedule = await self.db.has_snapshots(group_name)
                newly_added: dict[int, list[date]] = {1: [], 2: []}
                for day in sorted(x for x in schedules if x >= now.date()):
                    for subgroup in (1, 2):
                        filtered = schedules[day].for_subgroup(subgroup)
                        fingerprint = self.fingerprint(filtered)
                        lesson_count = len(filtered.lessons)
                        previous = await self.db.snapshot_record(
                            group_name, day, subgroup
                        )
                        await self.db.save_snapshot(
                            group_name, day, subgroup, fingerprint, lesson_count
                        )
                        # The first observation is a baseline, not a change.
                        if previous is None:
                            if established_schedule and lesson_count > 0:
                                newly_added[subgroup].append(day)
                            continue
                        if previous["lesson_count"] == 0 and lesson_count > 0:
                            newly_added[subgroup].append(day)
                        elif previous["fingerprint"] != fingerprint:
                            await self.broadcast(group_name, subgroup, format_change(filtered))
                for subgroup, days in newly_added.items():
                    if days:
                        await self.broadcast(
                            group_name, subgroup, format_new_schedule_period(days)
                        )
        except Exception as error:
            await self.error_reporter.report("Проверка изменений расписания", error)

    async def send_morning(self) -> None:
        now = datetime.now(self.timezone)
        today = now.date()
        try:
            for user in await self.db.active_users():
                schedule = await self.for_day(
                    user["group_name"], today, user["subgroup"], allow_stale=True
                )
                delivery_at = morning_delivery_time(
                    schedule, self.morning_time, self.timezone
                )
                if now < delivery_at:
                    continue
                if await self.db.claim_delivery(user["chat_id"], "morning", today):
                    sent = await self.safe_send(
                        user["chat_id"], "☀️ Доброе утро!\n\n" + format_schedule(schedule, "Сегодня")
                    )
                    if not sent:
                        await self.db.release_delivery(user["chat_id"], "morning", today)
        except SuspiciousEmptyScheduleError:
            return
        except Exception as error:
            await self.error_reporter.report("Утренняя рассылка", error)

    async def send_tomorrow_after_last_lesson(self) -> None:
        now = datetime.now(self.timezone)
        today = now.date()
        tomorrow = today + timedelta(days=1)
        try:
            for user in await self.db.active_users():
                current = await self.for_day(user["group_name"], today, user["subgroup"])
                last_end = max((x.ends_at for x in current.lessons), default=time(20, 10))
                if now.time().replace(second=0, microsecond=0) < last_end:
                    continue
                if await self.db.claim_delivery(user["chat_id"], "tomorrow", today):
                    schedule = await self.for_day(user["group_name"], tomorrow, user["subgroup"])
                    sent = await self.safe_send(
                        user["chat_id"],
                        "✨ Учебный день закончен!\n\n" + format_schedule(schedule, "Завтра"),
                    )
                    if not sent:
                        await self.db.release_delivery(user["chat_id"], "tomorrow", today)
        except SuspiciousEmptyScheduleError:
            return
        except Exception as error:
            await self.error_reporter.report("Рассылка расписания на завтра", error)

    async def send_next_lesson(self) -> None:
        """At lesson end, show the next lesson to users who enabled reminders."""
        now = datetime.now(self.timezone)
        current_minute = now.time().replace(second=0, microsecond=0)
        try:
            for user in await self.db.active_users():
                if not user["next_lesson_notifications"]:
                    continue
                schedule = await self.for_day(user["group_name"], now.date(), user["subgroup"])
                ended = next(
                    (lesson for lesson in schedule.lessons if lesson.ends_at == current_minute),
                    None,
                )
                if not ended:
                    continue
                following = next(
                    (lesson for lesson in schedule.lessons if lesson.starts_at >= ended.ends_at),
                    None,
                )
                if not following:
                    continue
                kind = f"next_lesson:{ended.ends_at:%H%M}"
                if not await self.db.claim_delivery(user["chat_id"], kind, now.date()):
                    continue
                message = (
                    "🔔 <b>Пара закончилась!</b>\n\n"
                    f"Следующая в <b>{following.starts_at:%H:%M}</b>:\n"
                    f"<b>{html.escape(following.subject)}</b>"
                )
                details = []
                if following.room:
                    details.append(f"📍 {html.escape(following.room)}")
                if following.teacher:
                    details.append(f"👤 {html.escape(following.teacher)}")
                if details:
                    message += "\n" + " · ".join(details)
                sent = await self.safe_send(user["chat_id"], message)
                if not sent:
                    await self.db.release_delivery(user["chat_id"], kind, now.date())
        except SuspiciousEmptyScheduleError:
            return
        except Exception as error:
            await self.error_reporter.report("Уведомления о следующей паре", error)

    async def broadcast(self, group_name: str, subgroup: int, text: str) -> None:
        for user in await self.db.active_users(subgroup, group_name):
            await self.safe_send(user["chat_id"], text)

    async def safe_send(self, chat_id: int, text: str) -> bool:
        try:
            await self.bot.send_message(chat_id, text)
            return True
        except TelegramRetryAfter as error:
            await asyncio.sleep(error.retry_after)
            try:
                await self.bot.send_message(chat_id, text)
                return True
            except Exception as retry_error:
                await self.error_reporter.report(
                    f"Повторная отправка Telegram-сообщения пользователю {chat_id}", retry_error
                )
                return False
        except TelegramForbiddenError:
            await self.db.deactivate_user(chat_id)
            return False
        except TelegramBadRequest as error:
            await self.error_reporter.report(
                f"Telegram отклонил сообщение пользователю {chat_id}", error
            )
            return False
        except TelegramNetworkError as error:
            await self.error_reporter.report(
                f"Сетевая ошибка отправки пользователю {chat_id}", error
            )
            return False
