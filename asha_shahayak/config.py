from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration; thresholds are intentionally tunable."""

    database_path: str = "asha_shahayak.sqlite3"
    registry_csv_path: str = "registry.csv"
    status_csv_path: str = "status.csv"
    confidence_high_threshold: float = 0.9
    confidence_medium_threshold: float = 0.6
    receipt_prefix: str = "ASHA"
    officer_token: str = ""
    api_key: str = ""
    meta_verify_token: str = Field(
        "",
        validation_alias=AliasChoices("ASHA_META_VERIFY_TOKEN", "WHATSAPP_VERIFY_TOKEN"),
    )
    meta_access_token: str = Field(
        "",
        validation_alias=AliasChoices("ASHA_META_ACCESS_TOKEN", "WHATSAPP_ACCESS_TOKEN"),
    )
    meta_phone_number_id: str = Field(
        "",
        validation_alias=AliasChoices("ASHA_META_PHONE_NUMBER_ID", "WHATSAPP_PHONE_NUMBER_ID"),
    )
    meta_app_secret: str = Field(
        "",
        validation_alias=AliasChoices("ASHA_META_APP_SECRET", "WHATSAPP_APP_SECRET"),
    )
    meta_graph_version: str = Field(
        "v20.0",
        validation_alias=AliasChoices("ASHA_META_GRAPH_VERSION", "WHATSAPP_GRAPH_VERSION"),
    )
    sarvam_api_key: str = ""
    sarvam_api_url: str = "https://api.sarvam.ai/speech-to-text"
    session_salt: str = Field(
        "",
        validation_alias=AliasChoices("ASHA_SESSION_SALT", "SESSION_SALT"),
    )

    model_config = SettingsConfigDict(
        env_prefix="ASHA_",
        env_file=".env",
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
