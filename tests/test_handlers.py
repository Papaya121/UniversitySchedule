import unittest

from bot.handlers import normalize_group
from bot.keyboards import donation_keyboard, main_keyboard


class NormalizeGroupTest(unittest.TestCase):
    def test_uppercases_and_removes_spaces(self) -> None:
        self.assertEqual(normalize_group("  ис2-261-об "), "ИС2-261-ОБ")
        self.assertEqual(normalize_group("ис2 - 261 - об"), "ИС2-261-ОБ")


class DonationKeyboardTest(unittest.TestCase):
    def test_donation_is_fifth_full_width_main_button(self) -> None:
        keyboard = main_keyboard().keyboard

        self.assertEqual([len(row) for row in keyboard], [2, 2, 1])
        self.assertEqual(keyboard[2][0].text, "❤️ Поддержать разработчика")

    def test_donation_button_opens_configured_url(self) -> None:
        url = "https://tbank.ru/cf/3o4Kr2VJXCE"
        button = donation_keyboard(url).inline_keyboard[0][0]

        self.assertEqual(button.text, "💚 Поддержать")
        self.assertEqual(button.url, url)


if __name__ == "__main__":
    unittest.main()
