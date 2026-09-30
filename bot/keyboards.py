from datetime import date

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


MAIN_KEYBOARD_VERSION = 2


def subgroup_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="1️⃣ Первая", callback_data="subgroup:1"),
        InlineKeyboardButton(text="2️⃣ Вторая", callback_data="subgroup:2"),
    ]])


def group_keyboard(groups: list[str]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=f"🎓 {group_name}", callback_data=f"group:use:{index}")
        for index, group_name in enumerate(groups)
    ]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def notification_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, уведомлять", callback_data="setup_notifications:1"),
        InlineKeyboardButton(text="❌ Нет", callback_data="setup_notifications:0"),
    ]])


def week_keyboard(weeks: list[date], labels: list[str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"week:{week.isoformat()}")]
        for week, label in zip(weeks, labels, strict=True)
    ])


def settings_keyboard(
    next_lesson_enabled: bool, lesson_start_enabled: bool,
    daily_schedule_enabled: bool, show_shared_groups: bool,
) -> InlineKeyboardMarkup:
    next_status = "✅ Включены" if next_lesson_enabled else "❌ Выключены"
    start_status = "✅ Включены" if lesson_start_enabled else "❌ Выключены"
    daily_status = "✅ Включена" if daily_schedule_enabled else "❌ Выключена"
    groups_status = "✅ Включено" if show_shared_groups else "❌ Выключено"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"Следующая пара: {next_status}",
            callback_data="notifications:toggle",
        )],
        [InlineKeyboardButton(
            text=f"Начало пары: {start_status}",
            callback_data="notifications:start:toggle",
        )],
        [InlineKeyboardButton(
            text=f"Утро и конец дня: {daily_status}",
            callback_data="notifications:daily:toggle",
        )],
        [InlineKeyboardButton(
            text=f"Группы на общих парах: {groups_status}",
            callback_data="settings:groups:toggle",
        )],
        [InlineKeyboardButton(text="🎓 Сменить группу", callback_data="settings:group")],
    ])


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Обновить статистику", callback_data="admin:stats")],
        [InlineKeyboardButton(text="📣 Создать рассылку", callback_data="admin:broadcast")],
        [InlineKeyboardButton(text="📥 Скачать пользователей", callback_data="admin:users_export")],
    ])


def broadcast_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Отправить всем", callback_data="admin:broadcast_confirm"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:broadcast_cancel"),
    ]])


def broadcast_audience_keyboard(groups: list[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        text="👥 Всем пользователям", callback_data="admin:audience:all"
    )]]
    group_buttons = [
        InlineKeyboardButton(text=f"🎓 {name}", callback_data=f"admin:audience:{index}")
        for index, name in enumerate(groups)
    ]
    rows.extend(
        group_buttons[index:index + 2]
        for index in range(0, len(group_buttons), 2)
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📚 Сегодня"), KeyboardButton(text="🌙 Завтра")],
            [KeyboardButton(text="🗓 Неделя"), KeyboardButton(text="⚙️ Настройки")],
            [KeyboardButton(text="❤️ Поддержать разработчика")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери, что показать",
    )


def donation_keyboard(donation_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Скопировать номер (СБП)",
                    copy_text=CopyTextButton(
                        text="+79086047055",
                    ),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💚 Поддержать (У кого Т-Банк)",
                    url=donation_url,
                ),
            ],
        ],
    )
