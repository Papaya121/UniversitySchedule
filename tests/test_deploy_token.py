import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "sync_token.py"


class SyncTokenTest(unittest.TestCase):
    def test_replaces_token_and_preserves_other_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "BOT_TOKEN=123456:old_token\nADMIN_IDS=42\nBOT_TOKEN=123456:duplicate\n",
                encoding="utf-8",
            )
            token = "987654321:new_token_value_1234567890"
            environment = os.environ.copy()
            environment["TELEGRAM_BOT_TOKEN_PROD"] = token

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(env_path)],
                env=environment, capture_output=True, text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(token, result.stdout + result.stderr)
            self.assertEqual(
                env_path.read_text(encoding="utf-8"),
                f"BOT_TOKEN={token}\nADMIN_IDS=42\n",
            )
            self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)

    def test_missing_secret_leaves_env_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            original = "BOT_TOKEN=123456:old_token\nADMIN_IDS=42\n"
            env_path.write_text(original, encoding="utf-8")
            environment = os.environ.copy()
            environment.pop("TELEGRAM_BOT_TOKEN_PROD", None)

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(env_path)],
                env=environment, capture_output=True, text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(env_path.read_text(encoding="utf-8"), original)

    def test_dev_token_uses_only_dev_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("BOT_TOKEN=123456:placeholder\nADMIN_IDS=42\n", encoding="utf-8")
            token = "987654321:dev_token_value_1234567890"
            environment = os.environ.copy()
            environment["TELEGRAM_BOT_TOKEN_DEV"] = token
            environment["TELEGRAM_BOT_TOKEN_PROD"] = "123456789:production_token_value"

            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(env_path), "TELEGRAM_BOT_TOKEN_DEV"],
                env=environment, capture_output=True, text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(token, result.stdout + result.stderr)
            self.assertEqual(
                env_path.read_text(encoding="utf-8"),
                f"BOT_TOKEN={token}\nDATABASE_PATH=data/bot.sqlite3\nBACKUP_DIRECTORY=backups\nADMIN_IDS=959026123\n",
            )
