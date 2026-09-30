import html
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ErrorEvent, Message, TelegramObject

from bot.database import Database
from bot.error_reporter import ErrorReporter, is_message_not_modified
from bot.formatters import MONTHS, format_schedule, human_date
from bot.keyboards import (
    MAIN_KEYBOARD_VERSION,
    donation_keyboard,
    group_keyboard,
    main_keyboard,
    notification_choice_keyboard,
    settings_keyboard,
    subgroup_keyboard,
    week_keyboard,
)
from bot.models import DaySchedule
from bot.service import ScheduleService

logger = logging.getLogger(__name__)


class ProfileSetup(StatesGroup):
    waiting_for_group = State()
    waiting_for_subgroup = State()
    waiting_for_notifications = State()


class MenuRefreshMiddleware(BaseMiddleware):
    def __init__(self, db: Database, reporter: ErrorReporter) -> None:
        self.db = db
        self.reporter = reporter

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            user = await self.db.get_user(event.chat.id)
            if user and await self.db.claim_menu_refresh(
                event.chat.id, MAIN_KEYBOARD_VERSION
            ):
                try:
                    command = (event.text or "").split(maxsplit=1)[0].split("@", 1)[0]
                    if command not in {"/start", "/help"}:
                        await event.answer(
                            "✨ Главное меню обновлено",
                            reply_markup=main_keyboard(),
                        )
                except Exception as error:
                    await self.db.release_menu_refresh(
                        event.chat.id, MAIN_KEYBOARD_VERSION
                    )
                    await self.reporter.report("Обновление главного меню", error)
        return await handler(event, data)


def normalize_group(value: str) -> str:
    """Group identifiers on the university site are uppercase and contain no spaces."""
    return "".join(value.split()).upper()


def calendar_week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_label(start: date) -> str:
    end = start + timedelta(days=6)
    if start.year == end.year and start.month == end.month:
        return f"{start.day:02d}–{end.day:02d} {MONTHS[end.month]}"
    if start.year == end.year:
        return f"{start.day:02d} {MONTHS[start.month]} – {end.day:02d} {MONTHS[end.month]}"
    return (
        f"{start.day:02d} {MONTHS[start.month]} {start.year} – "
        f"{end.day:02d} {MONTHS[end.month]} {end.year}"
    )


def available_week_starts(
    schedules: dict[date, DaySchedule], today: date
) -> list[date]:
    current = calendar_week_start(today)
    future = {
        calendar_week_start(day)
        for day, schedule in schedules.items()
        if day >= current + timedelta(days=7) and schedule.lessons
    }
    return [current, *sorted(future)]


def build_router(
    db: Database,
    service: ScheduleService,
    reporter: ErrorReporter,
    donation_url: str,
) -> Router:
    router = Router()
    router.message.outer_middleware(MenuRefreshMiddleware(db, reporter))

    async def ask_group(message: Message, state: FSMContext, mode: str) -> None:
        groups = await db.known_groups()
        await state.set_state(ProfileSetup.waiting_for_group)
        await state.set_data({"mode": mode, "suggested_groups": groups})
        await message.answer(
            "🎓 <b>Выбери свою учебную группу</b>\n\n"
            "Нажми кнопку или отправь название сообщением.\n"
            "Например: <code>ИМ2-262-ОБ</code>",
            reply_markup=group_keyboard(groups),
        )

    async def require_user(message: Message):
        user = await db.get_user(message.chat.id)
        if not user:
            await message.answer("Сначала создай профиль командой /start 👇")
        return user

    async def accept_group(message: Message, state: FSMContext, raw_group: str) -> None:
        group_name = normalize_group(raw_group)
        if not 2 <= len(group_name) <= 40:
            await message.answer("Название группы выглядит неверно. Попробуй ещё раз.")
            return
        checking = await message.answer(f"🔎 Проверяю группу <b>{html.escape(group_name)}</b>…")
        try:
            exists = await service.group_exists(group_name)
        except Exception as error:
            await reporter.report(f"Проверка группы {group_name}", error)
            await checking.edit_text("Сайт расписания сейчас недоступен. Попробуй ещё раз чуть позже 🙌")
            return
        if not exists:
            await checking.edit_text(
                f"Группа <b>{html.escape(group_name)}</b> не найдена на сайте ВГЛТУ.\n"
                "Проверь название и отправь его ещё раз."
            )
            return

        data = await state.get_data()
        await db.remember_group(group_name)
        if data.get("mode") == "settings" and not await db.get_user(message.chat.id):
            await state.update_data(mode="onboarding")
        await state.update_data(group_name=group_name)
        await state.set_state(ProfileSetup.waiting_for_subgroup)
        await checking.edit_text(
            f"Группа <b>{html.escape(group_name)}</b> найдена ✅\n\nТеперь выбери подгруппу:",
            reply_markup=subgroup_keyboard(),
        )

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        user = await db.get_user(message.chat.id)
        if user:
            await db.set_menu_version(message.chat.id, MAIN_KEYBOARD_VERSION)
            await message.answer(
                f"С возвращением! Твой профиль: <b>{html.escape(user['group_name'])}</b>, "
                f"<b>{user['subgroup']}-я подгруппа</b>.",
                reply_markup=main_keyboard(),
            )
            return
        name = html.escape(message.from_user.first_name) if message.from_user else "друг"
        await message.answer(
            f"Привет, <b>{name}</b>! 👋\n\n"
            "Я слежу за расписанием, сообщаю об изменениях и напоминаю о парах."
            "\n\n"
            "<i>Автор @Papaya121\n"
            "Если есть какие-либо пожелания, то напишите</i>"
        )
        await ask_group(message, state, "onboarding")

    @router.callback_query(ProfileSetup.waiting_for_group, F.data.startswith("group:use:"))
    async def use_suggested_group(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        groups = data.get("suggested_groups", [])
        try:
            index = int(callback.data.rsplit(":", 1)[1])
            group_name = groups[index]
        except (ValueError, IndexError, TypeError):
            await callback.answer("Отправь название группы сообщением", show_alert=True)
            return
        await callback.answer()
        await accept_group(callback.message, state, group_name)

    @router.message(ProfileSetup.waiting_for_group, F.text)
    async def receive_group(message: Message, state: FSMContext) -> None:
        await accept_group(message, state, message.text)

    @router.callback_query(
        ProfileSetup.waiting_for_subgroup,
        F.data.in_({"subgroup:1", "subgroup:2"}),
    )
    async def choose_subgroup(callback: CallbackQuery, state: FSMContext) -> None:
        subgroup = int(callback.data.split(":", 1)[1])
        data = await state.get_data()
        current = await db.get_user(callback.message.chat.id)
        group_name = data.get("group_name")
        if not group_name:
            await callback.answer("Сначала выбери группу через /start", show_alert=True)
            return
        user = callback.from_user
        if data.get("mode") == "onboarding" and current is None:
            await state.update_data(
                group_name=group_name,
                subgroup=subgroup,
                first_name=user.first_name,
                username=user.username,
            )
            await state.set_state(ProfileSetup.waiting_for_notifications)
            await callback.answer()
            await callback.message.edit_text(
                f"Группа: <b>{html.escape(group_name)}</b> ✅\n"
                f"Подгруппа: <b>{subgroup}</b> ✅\n\n"
                "🔔 Уведомлять о следующей паре в конце текущей?",
                reply_markup=notification_choice_keyboard(),
            )
            return
        if current is None:
            await callback.answer("Настройка устарела. Нажми /start", show_alert=True)
            await state.clear()
            return
        await db.upsert_user(
            callback.message.chat.id, group_name, subgroup, user.first_name, user.username
        )
        await state.clear()
        await db.set_menu_version(callback.message.chat.id, MAIN_KEYBOARD_VERSION)
        await callback.answer("Сохранено!")
        await callback.message.edit_text(
            f"Готово! <b>{html.escape(group_name)}</b>, <b>{subgroup}-я подгруппа</b> ✅\n"
            "Если расписание поменяется, я сразу напишу."
        )
        await callback.message.answer("Что показать?", reply_markup=main_keyboard())

    @router.callback_query(F.data.startswith("subgroup:"))
    async def stale_subgroup_choice(callback: CallbackQuery) -> None:
        await callback.answer(
            "Сначала выбери группу через /start или настройки", show_alert=True
        )

    @router.callback_query(
        ProfileSetup.waiting_for_notifications,
        F.data.startswith("setup_notifications:"),
    )
    async def choose_initial_notifications(callback: CallbackQuery, state: FSMContext) -> None:
        enabled = callback.data.endswith(":1")
        data = await state.get_data()
        group_name = data.get("group_name")
        subgroup = data.get("subgroup")
        if not group_name or subgroup not in (1, 2):
            await callback.answer("Настройка устарела. Нажми /start", show_alert=True)
            await state.clear()
            return
        await db.upsert_user(
            callback.message.chat.id,
            group_name,
            subgroup,
            data.get("first_name"),
            data.get("username"),
        )
        await db.set_next_lesson_notifications(callback.message.chat.id, enabled)
        await db.set_menu_version(callback.message.chat.id, MAIN_KEYBOARD_VERSION)
        await state.clear()
        status = "включены ✅" if enabled else "выключены ❌"
        await callback.answer("Профиль сохранён!")
        await callback.message.edit_text(
            "🎉 <b>Настройка завершена!</b>\n\n"
            f"Группа: <b>{html.escape(group_name)}</b>\n"
            f"Подгруппа: <b>{subgroup}</b>\n"
            f"Уведомления о следующей паре: <b>{status}</b>"
        )
        await callback.message.answer("Что показать?", reply_markup=main_keyboard())

    async def show_day(message: Message, offset: int, title: str) -> None:
        user = await require_user(message)
        if not user:
            return
        day = datetime.now(service.timezone).date() + timedelta(days=offset)
        try:
            schedule = await service.for_day(user["group_name"], day, user["subgroup"])
            await message.answer(format_schedule(schedule, title))
        except Exception as error:
            await reporter.report("Показ расписания на день", error)
            await message.answer("Не получилось связаться с сайтом расписания. Попробуй чуть позже 🙌")

    @router.message(Command("today"))
    @router.message(F.text == "📚 Сегодня")
    async def today(message: Message) -> None:
        await show_day(message, 0, "Сегодня")

    @router.message(Command("tomorrow"))
    @router.message(F.text == "🌙 Завтра")
    async def tomorrow(message: Message) -> None:
        await show_day(message, 1, "Завтра")

    @router.message(Command("week"))
    @router.message(F.text == "🗓 Неделя")
    async def week(message: Message) -> None:
        user = await require_user(message)
        if not user:
            return
        today = datetime.now(service.timezone).date()
        try:
            schedules = await service.schedules(user["group_name"])
            weeks = available_week_starts(schedules, today)
            if len(weeks) > 1:
                await message.answer(
                    f"🗓 Сегодня {today.day} {MONTHS[today.month]}. "
                    "<b>Какую неделю показать?</b>",
                    reply_markup=week_keyboard(weeks, [week_label(start) for start in weeks]),
                )
            else:
                await send_week(message, schedules, weeks[0], user)
        except Exception as error:
            await reporter.report("Показ расписания на неделю", error)
            await message.answer("Сайт расписания временно недоступен. Попробуй позже 🙌")

    async def send_week(
        message: Message, schedules: dict[date, DaySchedule], start: date, user
    ) -> None:
        await message.answer(
            f"🗓 <b>{week_label(start)} · {html.escape(user['group_name'])}</b>"
        )
        for offset in range(7):
            day = start + timedelta(days=offset)
            schedule = schedules.get(day)
            if schedule is None:
                await message.answer(
                    f"<b>{human_date(day)}</b>\nРасписание ещё не опубликовано."
                )
            else:
                await message.answer(
                    format_schedule(schedule.for_subgroup(user["subgroup"]))
                )

    @router.callback_query(F.data.startswith("week:"))
    async def choose_week(callback: CallbackQuery) -> None:
        await callback.answer()
        user = await db.get_user(callback.message.chat.id)
        if not user:
            await callback.message.answer("Сначала создай профиль командой /start 👇")
            return
        try:
            start = date.fromisoformat(callback.data.split(":", 1)[1])
            schedules = await service.schedules(user["group_name"])
            if start not in available_week_starts(
                schedules, datetime.now(service.timezone).date()
            ):
                await callback.message.answer("Выбор недели устарел. Нажми «🗓 Неделя» ещё раз.")
                return
            await send_week(callback.message, schedules, start, user)
        except ValueError:
            await callback.message.answer("Выбор недели устарел. Нажми «🗓 Неделя» ещё раз.")
        except Exception as error:
            await reporter.report("Показ выбранной недели", error)
            await callback.message.answer("Сайт расписания временно недоступен. Попробуй позже 🙌")

    @router.message(Command("settings"))
    @router.message(F.text == "⚙️ Настройки")
    async def settings(message: Message, state: FSMContext) -> None:
        await state.clear()
        user = await db.get_user(message.chat.id)
        if not user:
            await ask_group(message, state, "onboarding")
            return
        enabled = bool(user["next_lesson_notifications"])
        start_enabled = bool(user["lesson_start_notifications"])
        daily_enabled = bool(user["daily_schedule_notifications"])
        await message.answer(
            f"Группа: <b>{html.escape(user['group_name'])}</b>\n"
            f"Подгруппа: <b>{user['subgroup']}</b>\n\n"
            "Здесь можно изменить профиль и уведомления:",
            reply_markup=settings_keyboard(enabled, start_enabled, daily_enabled),
        )

    @router.callback_query(F.data == "settings:group")
    async def change_group(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await ask_group(callback.message, state, "settings")

    @router.callback_query(F.data == "notifications:toggle")
    async def toggle_notifications(callback: CallbackQuery) -> None:
        user = await db.get_user(callback.message.chat.id)
        if not user:
            await callback.answer("Сначала создай профиль через /start", show_alert=True)
            return
        enabled = await db.toggle_next_lesson_notifications(callback.message.chat.id)
        status = "включены ✅" if enabled else "выключены ❌"
        await callback.answer(f"Уведомления {status}")
        await callback.message.edit_reply_markup(reply_markup=settings_keyboard(
            enabled, bool(user["lesson_start_notifications"]),
            bool(user["daily_schedule_notifications"]),
        ))

    @router.callback_query(F.data == "notifications:start:toggle")
    async def toggle_start_notifications(callback: CallbackQuery) -> None:
        user = await db.get_user(callback.message.chat.id)
        if not user:
            await callback.answer("Сначала создай профиль через /start", show_alert=True)
            return
        enabled = await db.toggle_lesson_start_notifications(callback.message.chat.id)
        status = "включены ✅" if enabled else "выключены ❌"
        await callback.answer(f"Уведомления о начале пары {status}")
        await callback.message.edit_reply_markup(reply_markup=settings_keyboard(
            bool(user["next_lesson_notifications"]), enabled,
            bool(user["daily_schedule_notifications"]),
        ))

    @router.callback_query(F.data == "notifications:daily:toggle")
    async def toggle_daily_notifications(callback: CallbackQuery) -> None:
        user = await db.get_user(callback.message.chat.id)
        if not user:
            await callback.answer("Сначала создай профиль через /start", show_alert=True)
            return
        enabled = await db.toggle_daily_schedule_notifications(callback.message.chat.id)
        status = "включена ✅" if enabled else "выключена ❌"
        await callback.answer(f"Рассылка утром и в конце дня {status}")
        await callback.message.edit_reply_markup(reply_markup=settings_keyboard(
            bool(user["next_lesson_notifications"]),
            bool(user["lesson_start_notifications"]), enabled,
        ))

    @router.message(Command("donate"))
    @router.message(F.text == "❤️ Поддержать разработчика")
    async def donate(message: Message) -> None:
        await message.answer(
            "❤️ <b>Поддержать разработчика</b>\n\n"
            "Бот полностью бесплатный, я разрабатываю и поддерживаю его "
            "в свободное время.\n\n"
            "Если бот оказался полезен и хочется сказать спасибо, можешь "
            "поддержать его развитие любой суммой :)"
            "\n\n<b>Банк: </b> Т-Банк"
            "\n<b>Телефон: </b> +7 (908) 604-70-55"
            "\n<b>Получатель: </b> Владимир Ч.",
            reply_markup=donation_keyboard(donation_url),
        )

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        await message.answer(
            "<b>Что я умею</b>\n\n"
            "📚 /today — расписание на сегодня\n"
            "🌙 /tomorrow — на завтра\n"
            "🗓 /week — на неделю\n"
            "⚙️ /settings — группа, подгруппа и уведомления\n\n"
            "❤️ /donate — поддержать разработчика\n\n"
            "Изменения проверяются автоматически каждые 20 минут.",
            reply_markup=main_keyboard(),
        )

    @router.error()
    async def unhandled_error(event: ErrorEvent) -> bool:
        if is_message_not_modified(event.exception):
            return True
        await reporter.report("Необработанная ошибка Telegram-обработчика", event.exception)
        return True

    return router
