from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


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


def settings_keyboard(next_lesson_enabled: bool) -> InlineKeyboardMarkup:
    status = "✅ Включены" if next_lesson_enabled else "❌ Выключены"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1️⃣ Первая", callback_data="subgroup:1"),
            InlineKeyboardButton(text="2️⃣ Вторая", callback_data="subgroup:2"),
        ],
        [InlineKeyboardButton(
            text=f"Следующая пара: {status}",
            callback_data="notifications:toggle",
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
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери, что показать",
    )
