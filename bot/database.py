import asyncio
import hashlib
import json
import sqlite3
from datetime import date, datetime, time
from pathlib import Path
from uuid import uuid4

from bot.models import DaySchedule, Lesson


EMPTY_SCHEDULE_FINGERPRINT = hashlib.sha256(b"[]").hexdigest()


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()

    async def initialize(self, default_group: str = "ИС2-261-ОБ") -> None:
        async with self._lock:
            self._connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    chat_id INTEGER PRIMARY KEY,
                    group_name TEXT NOT NULL,
                    subgroup INTEGER NOT NULL CHECK(subgroup IN (1, 2)),
                    first_name TEXT,
                    username TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    next_lesson_notifications INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schedule_snapshots (
                    group_name TEXT NOT NULL,
                    day TEXT NOT NULL,
                    subgroup INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    lesson_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(group_name, day, subgroup)
                );
                CREATE TABLE IF NOT EXISTS schedule_cache (
                    group_name TEXT NOT NULL,
                    day TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(group_name, day)
                );
                CREATE TABLE IF NOT EXISTS schedule_guard_incidents (
                    group_name TEXT PRIMARY KEY,
                    active INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT,
                    resolved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    chat_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    day TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    PRIMARY KEY(chat_id, kind, day)
                );
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS known_groups (
                    group_name TEXT PRIMARY KEY,
                    first_seen_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS error_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    context TEXT NOT NULL,
                    error_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS broadcasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id INTEGER NOT NULL,
                    target_group TEXT,
                    recipients INTEGER NOT NULL,
                    delivered INTEGER NOT NULL,
                    failed INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            # Migration for databases created by an earlier bot version.
            columns = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(users)").fetchall()
            }
            if "next_lesson_notifications" not in columns:
                self._connection.execute(
                    "ALTER TABLE users ADD COLUMN next_lesson_notifications "
                    "INTEGER NOT NULL DEFAULT 1"
                )
            if "group_name" not in columns:
                self._connection.execute(
                    "ALTER TABLE users ADD COLUMN group_name TEXT NOT NULL DEFAULT ''"
                )
                self._connection.execute(
                    "UPDATE users SET group_name = ? WHERE group_name = ''", (default_group,)
                )

            snapshot_columns = {
                row["name"] for row in
                self._connection.execute("PRAGMA table_info(schedule_snapshots)").fetchall()
            }
            if "group_name" not in snapshot_columns:
                self._connection.executescript("""
                    ALTER TABLE schedule_snapshots RENAME TO old_schedule_snapshots;
                    CREATE TABLE schedule_snapshots (
                        group_name TEXT NOT NULL,
                        day TEXT NOT NULL,
                        subgroup INTEGER NOT NULL,
                        fingerprint TEXT NOT NULL,
                        lesson_count INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY(group_name, day, subgroup)
                    );
                """)
                self._connection.execute("""
                    INSERT INTO schedule_snapshots(
                        group_name, day, subgroup, fingerprint, lesson_count
                    )
                    SELECT ?, day, subgroup, fingerprint,
                           CASE WHEN fingerprint = ? THEN 0 ELSE 1 END
                    FROM old_schedule_snapshots
                """, (default_group, EMPTY_SCHEDULE_FINGERPRINT))
                self._connection.execute("DROP TABLE old_schedule_snapshots")
                snapshot_columns.add("lesson_count")
            if "lesson_count" not in snapshot_columns:
                self._connection.execute(
                    "ALTER TABLE schedule_snapshots "
                    "ADD COLUMN lesson_count INTEGER NOT NULL DEFAULT 0"
                )
                self._connection.execute("""
                    UPDATE schedule_snapshots
                    SET lesson_count = CASE WHEN fingerprint = ? THEN 0 ELSE 1 END
                """, (EMPTY_SCHEDULE_FINGERPRINT,))
            broadcast_columns = {
                row["name"] for row in
                self._connection.execute("PRAGMA table_info(broadcasts)").fetchall()
            }
            if "target_group" not in broadcast_columns:
                self._connection.execute(
                    "ALTER TABLE broadcasts ADD COLUMN target_group TEXT"
                )
            self._connection.execute(
                "INSERT OR IGNORE INTO bot_settings(key, value) VALUES ('suggested_group', ?)",
                (default_group,),
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO bot_settings(key, value) VALUES ('default_group', ?)",
                (default_group,),
            )
            now = datetime.now().isoformat(timespec="microseconds")
            self._connection.execute("""
                INSERT OR IGNORE INTO known_groups(group_name, first_seen_at, last_used_at)
                VALUES (?, ?, ?)
            """, (default_group, now, now))
            previous_suggestion = self._connection.execute(
                "SELECT value FROM bot_settings WHERE key = 'suggested_group'"
            ).fetchone()
            if previous_suggestion:
                self._connection.execute("""
                    INSERT OR IGNORE INTO known_groups(group_name, first_seen_at, last_used_at)
                    VALUES (?, ?, ?)
                """, (previous_suggestion["value"], now, now))
            self._connection.execute("""
                INSERT OR IGNORE INTO known_groups(group_name, first_seen_at, last_used_at)
                SELECT DISTINCT group_name, ?, ? FROM users WHERE group_name <> ''
            """, (now, now))
            self._connection.execute("""
                CREATE INDEX IF NOT EXISTS users_active_group_subgroup_idx
                ON users(active, group_name, subgroup)
            """)
            self._connection.commit()

    async def upsert_user(self, chat_id: int, group_name: str, subgroup: int, first_name: str | None, username: str | None) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        async with self._lock:
            self._connection.execute("""
                INSERT INTO users(chat_id, group_name, subgroup, first_name, username, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    group_name=excluded.group_name, subgroup=excluded.subgroup, first_name=excluded.first_name,
                    username=excluded.username, active=1, updated_at=excluded.updated_at
            """, (chat_id, group_name, subgroup, first_name, username, now, now))
            self._connection.commit()

    async def get_user(self, chat_id: int) -> sqlite3.Row | None:
        async with self._lock:
            return self._connection.execute(
                "SELECT * FROM users WHERE chat_id = ?", (chat_id,)
            ).fetchone()

    async def all_users(self) -> list[sqlite3.Row]:
        async with self._lock:
            return list(self._connection.execute(
                "SELECT * FROM users ORDER BY created_at, chat_id"
            ).fetchall())

    async def active_users(self, subgroup: int | None = None, group_name: str | None = None) -> list[sqlite3.Row]:
        query = "SELECT * FROM users WHERE active = 1"
        params: list[object] = []
        if group_name is not None:
            query += " AND group_name = ?"
            params.append(group_name)
        if subgroup is not None:
            query += " AND subgroup = ?"
            params.append(subgroup)
        async with self._lock:
            return list(self._connection.execute(query, tuple(params)).fetchall())

    async def active_groups(self) -> list[str]:
        async with self._lock:
            rows = self._connection.execute(
                "SELECT DISTINCT group_name FROM users WHERE active = 1"
            ).fetchall()
            return [row["group_name"] for row in rows]

    async def suggested_group(self) -> str:
        async with self._lock:
            row = self._connection.execute(
                "SELECT value FROM bot_settings WHERE key = 'suggested_group'"
            ).fetchone()
            return row["value"]

    async def set_suggested_group(self, group_name: str) -> None:
        await self.remember_group(group_name)

    async def known_groups(self, limit: int = 30) -> list[str]:
        async with self._lock:
            default_row = self._connection.execute(
                "SELECT value FROM bot_settings WHERE key = 'default_group'"
            ).fetchone()
            default_group = default_row["value"]
            rows = self._connection.execute("""
                SELECT group_name FROM known_groups
                ORDER BY CASE WHEN group_name = ? THEN 0 ELSE 1 END,
                         last_used_at DESC, group_name
                LIMIT ?
            """, (default_group, limit)).fetchall()
            return [row["group_name"] for row in rows]

    async def remember_group(self, group_name: str) -> None:
        now = datetime.now().isoformat(timespec="microseconds")
        async with self._lock:
            self._connection.execute("""
                INSERT INTO known_groups(group_name, first_seen_at, last_used_at)
                VALUES (?, ?, ?)
                ON CONFLICT(group_name) DO UPDATE SET last_used_at=excluded.last_used_at
            """, (group_name, now, now))
            # Kept for compatibility with databases created by the previous version.
            self._connection.execute("""
                INSERT INTO bot_settings(key, value) VALUES ('suggested_group', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """, (group_name,))
            self._connection.commit()

    async def deactivate_user(self, chat_id: int) -> None:
        async with self._lock:
            self._connection.execute("UPDATE users SET active = 0 WHERE chat_id = ?", (chat_id,))
            self._connection.commit()

    async def toggle_next_lesson_notifications(self, chat_id: int) -> bool:
        async with self._lock:
            self._connection.execute("""
                UPDATE users
                SET next_lesson_notifications = CASE next_lesson_notifications
                    WHEN 1 THEN 0 ELSE 1 END,
                    updated_at = ?
                WHERE chat_id = ?
            """, (datetime.now().isoformat(timespec="seconds"), chat_id))
            row = self._connection.execute(
                "SELECT next_lesson_notifications FROM users WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            self._connection.commit()
            return bool(row and row["next_lesson_notifications"])

    async def set_next_lesson_notifications(self, chat_id: int, enabled: bool) -> None:
        async with self._lock:
            self._connection.execute("""
                UPDATE users
                SET next_lesson_notifications = ?, updated_at = ?
                WHERE chat_id = ?
            """, (
                int(enabled), datetime.now().isoformat(timespec="seconds"), chat_id,
            ))
            self._connection.commit()

    async def record_error(self, context: str, error: BaseException) -> None:
        async with self._lock:
            self._connection.execute("""
                INSERT INTO error_log(context, error_type, message, created_at)
                VALUES (?, ?, ?, ?)
            """, (
                context,
                type(error).__name__,
                str(error)[:2000],
                datetime.now().isoformat(timespec="seconds"),
            ))
            self._connection.commit()

    async def record_broadcast(
        self, admin_id: int, recipients: int, delivered: int, failed: int,
        target_group: str | None = None,
    ) -> None:
        async with self._lock:
            self._connection.execute("""
                INSERT INTO broadcasts(
                    admin_id, target_group, recipients, delivered, failed, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                admin_id, target_group, recipients, delivered, failed,
                datetime.now().isoformat(timespec="seconds"),
            ))
            self._connection.commit()

    async def statistics(self) -> dict[str, object]:
        today = date.today().isoformat()
        async with self._lock:
            one = self._connection.execute
            total = one("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
            active = one("SELECT COUNT(*) AS n FROM users WHERE active = 1").fetchone()["n"]
            notifications = one(
                "SELECT COUNT(*) AS n FROM users "
                "WHERE active = 1 AND next_lesson_notifications = 1"
            ).fetchone()["n"]
            new_today = one(
                "SELECT COUNT(*) AS n FROM users WHERE created_at LIKE ?", (f"{today}%",)
            ).fetchone()["n"]
            groups = one("""
                SELECT group_name, COUNT(*) AS n
                FROM users WHERE active = 1
                GROUP BY group_name ORDER BY n DESC, group_name
            """).fetchall()
            subgroups = one("""
                SELECT subgroup, COUNT(*) AS n
                FROM users WHERE active = 1 GROUP BY subgroup ORDER BY subgroup
            """).fetchall()
            deliveries_today = one(
                "SELECT COUNT(*) AS n FROM deliveries WHERE day = ?", (today,)
            ).fetchone()["n"]
            errors_today = one(
                "SELECT COUNT(*) AS n FROM error_log WHERE created_at LIKE ?", (f"{today}%",)
            ).fetchone()["n"]
            broadcast = one("""
                SELECT COUNT(*) AS count, COALESCE(SUM(delivered), 0) AS delivered
                FROM broadcasts
            """).fetchone()
            return {
                "total": total,
                "active": active,
                "blocked": total - active,
                "notifications": notifications,
                "new_today": new_today,
                "groups": [(row["group_name"], row["n"]) for row in groups],
                "subgroups": [(row["subgroup"], row["n"]) for row in subgroups],
                "deliveries_today": deliveries_today,
                "errors_today": errors_today,
                "broadcast_count": broadcast["count"],
                "broadcast_delivered": broadcast["delivered"],
            }

    async def snapshot(self, group_name: str, day: date, subgroup: int) -> str | None:
        row = await self.snapshot_record(group_name, day, subgroup)
        return row["fingerprint"] if row else None

    async def snapshot_record(
        self, group_name: str, day: date, subgroup: int
    ) -> sqlite3.Row | None:
        async with self._lock:
            return self._connection.execute(
                "SELECT fingerprint, lesson_count FROM schedule_snapshots "
                "WHERE group_name = ? AND day = ? AND subgroup = ?",
                (group_name, day.isoformat(), subgroup),
            ).fetchone()

    async def save_snapshot(
        self, group_name: str, day: date, subgroup: int, fingerprint: str,
        lesson_count: int | None = None,
    ) -> None:
        if lesson_count is None:
            lesson_count = 0 if fingerprint == EMPTY_SCHEDULE_FINGERPRINT else 1
        async with self._lock:
            self._connection.execute("""
                INSERT INTO schedule_snapshots(
                    group_name, day, subgroup, fingerprint, lesson_count
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(group_name, day, subgroup) DO UPDATE SET
                    fingerprint=excluded.fingerprint,
                    lesson_count=excluded.lesson_count
            """, (
                group_name, day.isoformat(), subgroup, fingerprint, lesson_count,
            ))
            self._connection.commit()

    async def had_lessons_between(
        self, group_name: str, start: date, end: date
    ) -> bool:
        async with self._lock:
            row = self._connection.execute("""
                SELECT 1 FROM schedule_snapshots
                WHERE group_name = ? AND day BETWEEN ? AND ? AND lesson_count > 0
                LIMIT 1
            """, (group_name, start.isoformat(), end.isoformat())).fetchone()
            return row is not None

    async def has_snapshots(self, group_name: str) -> bool:
        async with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM schedule_snapshots WHERE group_name = ? LIMIT 1",
                (group_name,),
            ).fetchone()
            return row is not None

    async def save_schedule_cache(
        self, group_name: str, schedules: dict[date, DaySchedule]
    ) -> None:
        updated_at = datetime.now().isoformat(timespec="seconds")
        rows = []
        for day, schedule in schedules.items():
            payload = json.dumps([
                {
                    "starts_at": lesson.starts_at.isoformat(timespec="minutes"),
                    "ends_at": lesson.ends_at.isoformat(timespec="minutes"),
                    "subject": lesson.subject,
                    "subgroup": lesson.subgroup,
                    "room": lesson.room,
                    "teacher": lesson.teacher,
                }
                for lesson in schedule.lessons
            ], ensure_ascii=False, separators=(",", ":"))
            rows.append((group_name, day.isoformat(), payload, updated_at))
        async with self._lock:
            self._connection.execute(
                "DELETE FROM schedule_cache WHERE group_name = ?", (group_name,)
            )
            self._connection.executemany("""
                INSERT INTO schedule_cache(group_name, day, payload, updated_at)
                VALUES (?, ?, ?, ?)
            """, rows)
            self._connection.commit()

    async def load_schedule_cache(self, group_name: str) -> dict[date, DaySchedule]:
        async with self._lock:
            rows = self._connection.execute("""
                SELECT day, payload FROM schedule_cache
                WHERE group_name = ? ORDER BY day
            """, (group_name,)).fetchall()
        result: dict[date, DaySchedule] = {}
        for row in rows:
            day = date.fromisoformat(row["day"])
            lessons = tuple(
                Lesson(
                    starts_at=time.fromisoformat(item["starts_at"]),
                    ends_at=time.fromisoformat(item["ends_at"]),
                    subject=item["subject"],
                    subgroup=item["subgroup"],
                    room=item["room"],
                    teacher=item["teacher"],
                )
                for item in json.loads(row["payload"])
            )
            result[day] = DaySchedule(day, lessons)
        return result

    async def start_empty_schedule_incident(self, group_name: str) -> bool:
        now = datetime.now().isoformat(timespec="seconds")
        async with self._lock:
            row = self._connection.execute(
                "SELECT active FROM schedule_guard_incidents WHERE group_name = ?",
                (group_name,),
            ).fetchone()
            if row and row["active"]:
                return False
            self._connection.execute("""
                INSERT INTO schedule_guard_incidents(
                    group_name, active, started_at, resolved_at
                ) VALUES (?, 1, ?, NULL)
                ON CONFLICT(group_name) DO UPDATE SET
                    active=1, started_at=excluded.started_at, resolved_at=NULL
            """, (group_name, now))
            self._connection.commit()
            return True

    async def resolve_empty_schedule_incident(self, group_name: str) -> bool:
        now = datetime.now().isoformat(timespec="seconds")
        async with self._lock:
            cursor = self._connection.execute("""
                UPDATE schedule_guard_incidents
                SET active=0, resolved_at=?
                WHERE group_name=? AND active=1
            """, (now, group_name))
            self._connection.commit()
            return cursor.rowcount == 1

    async def release_empty_schedule_incident(self, group_name: str) -> None:
        """Allow retrying an admin alert that could not be delivered."""
        async with self._lock:
            self._connection.execute(
                "DELETE FROM schedule_guard_incidents "
                "WHERE group_name = ? AND active = 1",
                (group_name,),
            )
            self._connection.commit()

    async def claim_delivery(self, chat_id: int, kind: str, day: date) -> bool:
        async with self._lock:
            cursor = self._connection.execute("""
                INSERT OR IGNORE INTO deliveries(chat_id, kind, day, sent_at)
                VALUES (?, ?, ?, ?)
            """, (chat_id, kind, day.isoformat(), datetime.now().isoformat(timespec="seconds")))
            self._connection.commit()
            return cursor.rowcount == 1

    async def release_delivery(self, chat_id: int, kind: str, day: date) -> None:
        """Allow a failed notification to be retried on the next scheduler tick."""
        async with self._lock:
            self._connection.execute(
                "DELETE FROM deliveries WHERE chat_id = ? AND kind = ? AND day = ?",
                (chat_id, kind, day.isoformat()),
            )
            self._connection.commit()

    async def backup_to(self, destination: Path) -> Path:
        """Create a consistent SQLite snapshot, including data still stored in WAL."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{uuid4().hex}.tmp"
        )

        try:
            async with self._lock:
                target = sqlite3.connect(temporary)
                try:
                    self._connection.backup(target)
                    result = target.execute("PRAGMA integrity_check").fetchone()
                    if not result or result[0] != "ok":
                        raise sqlite3.DatabaseError(
                            f"Backup integrity check failed: {result!r}"
                        )
                finally:
                    target.close()
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return destination

    async def close(self) -> None:
        async with self._lock:
            self._connection.close()
