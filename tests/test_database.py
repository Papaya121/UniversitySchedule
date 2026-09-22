import tempfile
import unittest
import sqlite3
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.backup import BackupManager
from bot.database import Database


class DatabaseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.sqlite3")
        await self.db.initialize()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.temp_dir.cleanup()

    async def test_saves_user_and_changes_subgroup(self) -> None:
        await self.db.upsert_user(42, "ИС2-261-ОБ", 1, "Иван", "ivan")
        await self.db.upsert_user(42, "ПИ-101", 2, "Иван", "ivan")

        user = await self.db.get_user(42)
        self.assertEqual(user["subgroup"], 2)
        self.assertEqual(user["group_name"], "ПИ-101")
        self.assertEqual(len(await self.db.active_users()), 1)

    async def test_all_users_includes_inactive_profiles(self) -> None:
        await self.db.upsert_user(42, "ИС2-261-ОБ", 1, "Иван", "ivan")
        await self.db.upsert_user(43, "ПИ-101", 2, "Анна", None)
        await self.db.deactivate_user(43)

        users = await self.db.all_users()

        self.assertEqual([user["chat_id"] for user in users], [42, 43])
        self.assertEqual(users[1]["active"], 0)

    async def test_delivery_can_be_claimed_only_once_and_released(self) -> None:
        day = date(2026, 9, 4)
        self.assertTrue(await self.db.claim_delivery(42, "morning", day))
        self.assertFalse(await self.db.claim_delivery(42, "morning", day))
        await self.db.release_delivery(42, "morning", day)
        self.assertTrue(await self.db.claim_delivery(42, "morning", day))

    async def test_menu_refresh_is_claimed_once_per_version(self) -> None:
        await self.db.upsert_user(42, "ИС2-261-ОБ", 1, "Иван", "ivan")

        self.assertTrue(await self.db.claim_menu_refresh(42, 2))
        self.assertFalse(await self.db.claim_menu_refresh(42, 2))
        await self.db.release_menu_refresh(42, 2)
        self.assertTrue(await self.db.claim_menu_refresh(42, 2))
        await self.db.set_menu_version(42, 3)
        self.assertFalse(await self.db.claim_menu_refresh(42, 2))

    async def test_stores_schedule_fingerprint(self) -> None:
        day = date(2026, 9, 4)
        self.assertIsNone(await self.db.snapshot("ИС2-261-ОБ", day, 1))
        await self.db.save_snapshot("ИС2-261-ОБ", day, 1, "abc")
        self.assertEqual(await self.db.snapshot("ИС2-261-ОБ", day, 1), "abc")
        self.assertIsNone(await self.db.snapshot("ДРУГАЯ-ГРУППА", day, 1))

    async def test_toggles_next_lesson_notifications(self) -> None:
        await self.db.upsert_user(42, "ИС2-261-ОБ", 1, "Иван", "ivan")
        user = await self.db.get_user(42)
        self.assertEqual(user["next_lesson_notifications"], 1)

        self.assertFalse(await self.db.toggle_next_lesson_notifications(42))
        user = await self.db.get_user(42)
        self.assertEqual(user["next_lesson_notifications"], 0)

        self.assertTrue(await self.db.toggle_next_lesson_notifications(42))

        await self.db.set_next_lesson_notifications(42, False)
        user = await self.db.get_user(42)
        self.assertEqual(user["next_lesson_notifications"], 0)

    async def test_updates_suggested_group(self) -> None:
        self.assertEqual(await self.db.suggested_group(), "ИС2-261-ОБ")
        await self.db.set_suggested_group("ПИ-101")
        self.assertEqual(await self.db.suggested_group(), "ПИ-101")
        await self.db.remember_group("ИМ2-262-ОБ")
        self.assertEqual(
            await self.db.known_groups(),
            ["ИС2-261-ОБ", "ИМ2-262-ОБ", "ПИ-101"],
        )

    async def test_collects_admin_statistics(self) -> None:
        await self.db.upsert_user(42, "ИС2-261-ОБ", 1, "Иван", "ivan")
        await self.db.upsert_user(43, "ПИ-101", 2, "Анна", "anna")
        await self.db.deactivate_user(43)
        await self.db.record_error("test", ValueError("example"))
        await self.db.record_broadcast(42, 1, 1, 0)

        stats = await self.db.statistics()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["active"], 1)
        self.assertEqual(stats["blocked"], 1)
        self.assertEqual(stats["errors_today"], 1)
        self.assertEqual(stats["broadcast_delivered"], 1)

    async def test_creates_restorable_backup_and_removes_expired_files(self) -> None:
        await self.db.upsert_user(42, "ИС2-261-ОБ", 1, "Иван", "ivan")
        backup_directory = Path(self.temp_dir.name) / "backups"
        manager = BackupManager(self.db, backup_directory, retention_days=14)
        now = datetime(2026, 9, 22, 3, 0, tzinfo=ZoneInfo("Europe/Moscow"))

        expired = backup_directory / "bot-20260801-030000.sqlite3"
        backup_directory.mkdir()
        expired.touch()
        old_timestamp = (now - timedelta(days=15)).timestamp()
        os.utime(expired, (old_timestamp, old_timestamp))

        backup = await manager.create(now)

        self.assertTrue(backup.exists())
        self.assertFalse(expired.exists())
        restored = sqlite3.connect(backup)
        try:
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(restored.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
        finally:
            restored.close()


class LegacyDatabaseMigrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_adds_groups_without_losing_existing_user(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript("""
                CREATE TABLE users (
                    chat_id INTEGER PRIMARY KEY, subgroup INTEGER NOT NULL,
                    first_name TEXT, username TEXT, active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                INSERT INTO users VALUES (42, 2, 'Иван', 'ivan', 1, 'now', 'now');
                CREATE TABLE schedule_snapshots (
                    day TEXT NOT NULL, subgroup INTEGER NOT NULL, fingerprint TEXT NOT NULL,
                    PRIMARY KEY(day, subgroup)
                );
                INSERT INTO schedule_snapshots VALUES ('2026-09-04', 2, 'abc');
            """)
            connection.commit()
            connection.close()

            database = Database(path)
            await database.initialize("ИС2-261-ОБ")
            user = await database.get_user(42)
            self.assertEqual(user["group_name"], "ИС2-261-ОБ")
            self.assertEqual(user["subgroup"], 2)
            self.assertEqual(user["menu_version"], 0)
            self.assertEqual(
                await database.snapshot("ИС2-261-ОБ", date(2026, 9, 4), 2), "abc"
            )
            migrated = await database.snapshot_record(
                "ИС2-261-ОБ", date(2026, 9, 4), 2
            )
            self.assertEqual(migrated["lesson_count"], 1)
            self.assertEqual(await database.known_groups(), ["ИС2-261-ОБ"])
            await database.close()


if __name__ == "__main__":
    unittest.main()
