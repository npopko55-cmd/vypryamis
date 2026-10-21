from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator
from urllib.parse import urlparse


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: str = "development"
    database_url: str = "sqlite:///./vypryamis.db"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "development-only-change-before-production-32chars"
    public_url: str = "http://localhost:5173"
    gc_account_url: str = ""
    gc_api_key: str = ""
    gc_webhook_secret: str = ""
    gc_base_offer: str = ""
    gc_full_offer: str = ""
    mail_api_key: str = ""
    mail_sender: str = ""
    mail_api_url: str = "https://go2.unisender.ru/ru/transactional/api/v1/email/send.json"
    kinescope_username: str = ""
    kinescope_password: str = ""
    completion_seconds: int = 180
    reminder_hour: int = 9
    session_days: int = 90
    base_price: int = 99000
    full_price: int = 199000
    legal_name: str = ""
    legal_inn: str = ""
    support_email: str = ""

    @model_validator(mode="after")
    def production_checks(self):
        if self.environment == "production":
            if "development" in self.secret_key or self.secret_key.startswith("CHANGE") or len(self.secret_key) < 40:
                raise ValueError("Set a random SECRET_KEY of at least 40 characters")
            if not self.database_url.startswith("postgresql"):
                raise ValueError("Production requires PostgreSQL")
            if not self.public_url.startswith("https://"):
                raise ValueError("Production requires HTTPS")
            if self.gc_account_url and urlparse(self.gc_account_url).scheme != "https":
                raise ValueError("GetCourse requires HTTPS")
        return self

    @property
    def production(self):
        return self.environment == "production"


@lru_cache
def settings():
    return Settings()
