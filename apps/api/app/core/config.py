from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173"
    database_url: str = "postgresql+psycopg://crisisx:change_me@localhost:5432/crisisx"
    data_storage_root: str = "./data/storage"

    # Phase 12 (AI Assistant) -- optional. Absent by default: the
    # assistant works fully offline against the deterministic
    # FakeProvider (see app/services/assistant/provider.py) with no key
    # configured. No vendor SDK reads these yet -- see that module's
    # docstring for why plugging in a real provider is deferred.
    ai_provider_api_key: str | None = None
    ai_provider_model: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
