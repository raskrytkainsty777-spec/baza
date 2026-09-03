"""Настройки из окружения.

На сервере переменные приходят из /opt/baza/.env через EnvironmentFile
в systemd; локально — из .env в корне репозитория. Всё, что секретно,
живёт только там, в git не попадает.
"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore",
    )

    database_url: str
    admin_token: str

    # внешние сервисы
    parserim_key: str = ""
    apify_token: str = ""
    openrouter_key: str = ""
    ai_model_cheap: str = "anthropic/claude-haiku-4.5"
    ai_model_smart: str = "anthropic/claude-sonnet-5"

    # пробив на старом сервере
    probe_base_url: str = "http://132.243.114.17"
    probe_hook_token: str = ""
    probe_callback_secret: str = ""

    # CRM: наружу — лиды, внутрь — статусы
    crm_webhook_url: str = ""
    crm_webhook_secret: str = ""
    inbox_secret: str = ""

    # оповещения
    telegram_bot_token: str = ""
    telegram_alert_chat_id: str = ""

    # лимиты
    apify_daily_cap_usd: float = 10.0
    parserim_max_parallel_jobs: int = Field(3, ge=1)
    parserim_rpm: int = 150   # у них 180, держим запас

    tz_display: str = "Europe/Moscow"


settings = Settings()
