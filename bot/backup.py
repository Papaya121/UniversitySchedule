import logging
from datetime import datetime, timedelta
from pathlib import Path

from bot.database import Database


logger = logging.getLogger(__name__)


class BackupManager:
    def __init__(
        self, database: Database, directory: Path, retention_days: int
    ) -> None:
        self.database = database
        self.directory = directory
        self.retention_days = retention_days

    async def create(self, now: datetime) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / f"bot-{now:%Y%m%d-%H%M%S}.sqlite3"
        await self.database.backup_to(destination)
        self.remove_expired(now)
        logger.info("Database backup created: %s", destination)
        return destination

    def remove_expired(self, now: datetime) -> list[Path]:
        cutoff = now - timedelta(days=self.retention_days)
        removed: list[Path] = []
        for path in self.directory.glob("bot-*.sqlite3"):
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo)
            if modified < cutoff:
                path.unlink()
                removed.append(path)
        return removed
