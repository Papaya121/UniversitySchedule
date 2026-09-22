import asyncio
import html

from aiogram import Bot, F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.database import Database
from bot.error_reporter import ErrorReporter
from bot.error_reporter import is_message_not_modified
from bot.keyboards import (
    admin_keyboard,
    broadcast_audience_keyboard,
    broadcast_confirmation_keyboard,
)


class AdminBroadcast(StatesGroup):
    choosing_audience = State()
    waiting_for_message = State()
    waiting_for_confirmation = State()


def build_admin_router(
    bot: Bot, db: Database, admin_ids: set[int], reporter: ErrorReporter
) -> Router:
    router = Router(name="admin")

    def is_admin(user_id: int) -> bool:
        return user_id in admin_ids

    async def deny_callback(callback: CallbackQuery) -> None:
        await callback.answer("Доступно только администратору", show_alert=True)

    async def statistics_text() -> str:
        stats = await db.statistics()
        groups = stats["groups"]
        group_lines = "\n".join(
            f"• {html.escape(name)} — {count}" for name, count in groups[:20]
        ) or "• пока нет"
        if len(groups) > 20:
            group_lines += f"\n• …ещё {len(groups) - 20} групп"
        subgroup_counts = dict(stats["subgroups"])
        return (
            "👑 <b>Панель администратора</b>\n\n"
            f"👥 Всего профилей: <b>{stats['total']}</b>\n"
            f"✅ Активных: <b>{stats['active']}</b>\n"
            f"🚫 Заблокировали бота: <b>{stats['blocked']}</b>\n"
            f"🆕 Новых сегодня: <b>{stats['new_today']}</b>\n"
            f"1️⃣ Первая подгруппа: <b>{subgroup_counts.get(1, 0)}</b>\n"
            f"2️⃣ Вторая подгруппа: <b>{subgroup_counts.get(2, 0)}</b>\n"
            f"🔔 Уведомления о парах включены: <b>{stats['notifications']}</b>\n"
            f"✉️ Автоуведомлений сегодня: <b>{stats['deliveries_today']}</b>\n"
            f"🚨 Ошибок сегодня: <b>{stats['errors_today']}</b>\n"
            f"📣 Массовых рассылок: <b>{stats['broadcast_count']}</b> "
            f"({stats['broadcast_delivered']} доставлено)\n\n"
            f"<b>Группы:</b>\n{group_lines}"
            f"\nВерсия 0.2.0"
        )

    @router.message(Command("myid"))
    async def my_id(message: Message) -> None:
        await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>")

    @router.message(Command("admin"))
    async def admin_menu(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            await message.answer("⛔ Эта команда доступна только администратору.")
            return
        await state.clear()
        await message.answer(await statistics_text(), reply_markup=admin_keyboard())

    @router.callback_query(F.data == "admin:stats")
    async def refresh_statistics(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await deny_callback(callback)
            return
        try:
            await callback.message.edit_text(
                await statistics_text(), reply_markup=admin_keyboard()
            )
            await callback.answer("Обновлено")
        except TelegramBadRequest as error:
            if is_message_not_modified(error):
                await callback.answer("Статистика пока не изменилась")
                return
            raise

    @router.callback_query(F.data == "admin:broadcast")
    async def request_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await deny_callback(callback)
            return
        groups = await db.active_groups()
        await state.set_state(AdminBroadcast.choosing_audience)
        await state.set_data({"audience_groups": groups})
        await callback.answer()
        await callback.message.answer(
            "📣 <b>Кому отправить сообщение?</b>",
            reply_markup=broadcast_audience_keyboard(groups),
        )

    @router.callback_query(
        AdminBroadcast.choosing_audience,
        F.data.startswith("admin:audience:"),
    )
    async def choose_audience(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await deny_callback(callback)
            return
        value = callback.data.rsplit(":", 1)[1]
        data = await state.get_data()
        groups = data.get("audience_groups", [])
        if value == "all":
            target_group = None
            audience_label = "всем активным пользователям"
        else:
            try:
                target_group = groups[int(value)]
            except (ValueError, IndexError, TypeError):
                await callback.answer("Список групп устарел", show_alert=True)
                return
            audience_label = f"группе {target_group}"
        await state.update_data(
            target_group=target_group,
            audience_label=audience_label,
        )
        await state.set_state(AdminBroadcast.waiting_for_message)
        await callback.answer()
        await callback.message.edit_text(
            f"Выбрана рассылка <b>{html.escape(audience_label)}</b>.\n\n"
            "Теперь отправь текст, фото, видео, документ или другое сообщение.\n\n"
            "Для отмены: /cancel"
        )

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            return
        await state.clear()
        await message.answer("Рассылка отменена.", reply_markup=admin_keyboard())

    @router.message(AdminBroadcast.waiting_for_message)
    async def preview_broadcast(message: Message, state: FSMContext) -> None:
        if not is_admin(message.from_user.id):
            await state.clear()
            return
        await state.update_data(source_chat_id=message.chat.id, source_message_id=message.message_id)
        await state.set_state(AdminBroadcast.waiting_for_confirmation)
        await message.answer("Вот как выглядит сообщение:")
        await bot.copy_message(message.chat.id, message.chat.id, message.message_id)
        target_group = (await state.get_data()).get("target_group")
        recipients = len(await db.active_users(group_name=target_group))
        audience_label = (await state.get_data()).get(
            "audience_label", "всем активным пользователям"
        )
        await message.answer(
            f"Отправить его <b>{html.escape(audience_label)}</b>? "
            f"Получателей: <b>{recipients}</b>.",
            reply_markup=broadcast_confirmation_keyboard(),
        )

    @router.callback_query(F.data == "admin:broadcast_cancel")
    async def cancel_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await deny_callback(callback)
            return
        await state.clear()
        await callback.answer("Отменено")
        await callback.message.edit_text("Рассылка отменена.")

    @router.callback_query(
        AdminBroadcast.waiting_for_confirmation,
        F.data == "admin:broadcast_confirm",
    )
    async def confirm_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_admin(callback.from_user.id):
            await deny_callback(callback)
            return
        data = await state.get_data()
        source_chat_id = data.get("source_chat_id")
        source_message_id = data.get("source_message_id")
        target_group = data.get("target_group")
        if not source_chat_id or not source_message_id:
            await state.clear()
            await callback.answer("Сообщение устарело", show_alert=True)
            return
        await state.clear()
        await callback.answer("Рассылка началась")
        await callback.message.edit_text("⏳ Отправляю сообщение пользователям…")

        users = await db.active_users(group_name=target_group)
        delivered = 0
        failed = 0
        first_unexpected_error: Exception | None = None
        for user in users:
            try:
                await bot.copy_message(user["chat_id"], source_chat_id, source_message_id)
                delivered += 1
            except TelegramRetryAfter as error:
                await asyncio.sleep(error.retry_after)
                try:
                    await bot.copy_message(user["chat_id"], source_chat_id, source_message_id)
                    delivered += 1
                except Exception as retry_error:
                    failed += 1
                    first_unexpected_error = first_unexpected_error or retry_error
            except TelegramForbiddenError:
                failed += 1
                await db.deactivate_user(user["chat_id"])
            except (TelegramBadRequest, TelegramNetworkError) as error:
                failed += 1
                first_unexpected_error = first_unexpected_error or error
            except Exception as error:
                failed += 1
                first_unexpected_error = first_unexpected_error or error

        await db.record_broadcast(
            callback.from_user.id, len(users), delivered, failed, target_group
        )
        await callback.message.edit_text(
            "✅ <b>Рассылка завершена</b>\n\n"
            f"Доставлено: <b>{delivered}</b>\n"
            f"Не доставлено: <b>{failed}</b>",
            reply_markup=admin_keyboard(),
        )
        if first_unexpected_error:
            await reporter.report("Ошибки массовой рассылки", first_unexpected_error)

    return router
