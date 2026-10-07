from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration; thresholds are intentionally tunable."""

    database_path: str = "asha_shahayak.sqlite3"
    confidence_high_threshold: float = 0.9
    confidence_medium_threshold: float = 0.6
    receipt_prefix: str = "ASHA"
    officer_token: str = "change-me"
    meta_verify_token: str = ""
    meta_access_token: str = ""
    meta_phone_number_id: str = ""
    sarvam_api_key: str = ""
    sarvam_api_url: str = "https://api.sarvam.ai/speech-to-text"

    model_config = SettingsConfigDict(env_prefix="ASHA_", env_file=".env")


@lru_cache
def get_settings() -> Settings:
    return Settings()
