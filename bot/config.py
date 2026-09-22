from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = Field(min_length=20)
    admin_ids_value: str = Field(default="", alias="ADMIN_IDS")
    group_name: str = "ИС2-261-ОБ"
    schedule_url: str = "https://kis.vgltu.ru/schedule"
    timezone: str = "Europe/Moscow"
    database_path: Path = Path("data/bot.sqlite3")
    backup_directory: Path = Path("backups")
    backup_retention_days: int = Field(default=14, ge=1, le=365)
    backup_hour: int = Field(default=3, ge=0, le=23)
    backup_minute: int = Field(default=0, ge=0, le=59)
    check_interval_minutes: int = Field(default=20, ge=1, le=1440)
    morning_hour: int = Field(default=9, ge=0, le=23)
    morning_minute: int = Field(default=0, ge=0, le=59)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def admin_ids(self) -> set[int]:
        result: set[int] = set()
        for item in self.admin_ids_value.split(","):
            item = item.strip()
            if item:
                result.add(int(item))
        return result
