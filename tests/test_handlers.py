import unittest

from bot.handlers import normalize_group


class NormalizeGroupTest(unittest.TestCase):
    def test_uppercases_and_removes_spaces(self) -> None:
        self.assertEqual(normalize_group("  ис2-261-об "), "ИС2-261-ОБ")
        self.assertEqual(normalize_group("ис2 - 261 - об"), "ИС2-261-ОБ")


if __name__ == "__main__":
    unittest.main()
