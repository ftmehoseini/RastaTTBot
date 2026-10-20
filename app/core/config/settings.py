"""Infrastructure settings and secrets, read from environment variables or `.env`."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotPlatform(StrEnum):
    TELEGRAM = "telegram"
    BALE = "bale"
    SOROUSH = "soroush"


PLATFORM_API_BASES: dict[BotPlatform, str] = {
    BotPlatform.TELEGRAM: "https://api.telegram.org",
    BotPlatform.BALE: "https://tapi.bale.ai",
    BotPlatform.SOROUSH: "https://api.splus.ir",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    log_format: str = "json"

    bot_token: SecretStr = SecretStr("")
    bot_platform: BotPlatform = BotPlatform.TELEGRAM
    bot_api_base_url: str = ""
    bootstrap_super_admin_ids: str = ""

    database_url: str = "postgresql+asyncpg://rasta_bot:change-me@localhost:5432/rasta_bot"
    database_echo: bool = False
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    reports_dir: Path = Path("reports")
    upload_dir: Path = Path("uploads")
    max_upload_bytes: int = Field(default=2 * 1024 * 1024, gt=0)

    health_host: str = "0.0.0.0"
    health_port: int = 8080

    policy_dir: Path = Path("config")
    policy_file: str = ""

    @field_validator("bot_api_base_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.strip().rstrip("/")

    @property
    def api_base_url(self) -> str:
        return self.bot_api_base_url or PLATFORM_API_BASES[self.bot_platform]

    @property
    def super_admin_ids(self) -> frozenset[int]:
        parts = (p.strip() for p in self.bootstrap_super_admin_ids.split(","))
        return frozenset(int(p) for p in parts if p)


@lru_cache
def get_settings() -> Settings:
    return Settings()
