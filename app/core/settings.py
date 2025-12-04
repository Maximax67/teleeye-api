from typing import Optional
from pydantic import HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_TITLE: str = "TeleEye"
    APP_VERSION: str = "1.0.0"

    ALLOWED_ORIGINS: str
    DATABASE_URL: SecretStr

    API_URL: HttpUrl
    JWT_SECRET: SecretStr
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRES_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRES_DAYS: int = 30
    JWT_ISSUER: Optional[str] = None
    JWT_AUDIENCE: Optional[str] = None

    AES_TOKEN: SecretStr
    AES_TOKEN_SALT: Optional[SecretStr] = None

    MAX_USER_SESSIONS: int = 30
    MAX_USER_BOTS: int = 10
    MAX_USER_BOT_LINKS: int = 30

    OTP_LENGTH: int = 6
    OTP_TTL: int = 60

    MAILER_URL: str
    MAILER_TOKEN: str

    TELEGRAM_API_URL: HttpUrl = HttpUrl("https://api.telegram.org/bot")
    TELEGRAM_API_FILE_URL: HttpUrl = HttpUrl("https://api.telegram.org/file/bot")

    WEBHOOK_REDIRECT_TIMEOUT: float = 10.0
    TELEGRAM_API_REDIRECT_TIMEOUT: float = 10.0

    ATTACH_FRONTEND: bool = False
    FRONTEND_PATH: str = "./static"
    API_PREFIX: str = ""

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings(**{})
