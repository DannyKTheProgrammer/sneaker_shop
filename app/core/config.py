# Конфигурация: переменные окружения главнее файла .env.
# .env ищется от расположения файла, а не от cwd.

from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

# HS256 на коротком ключе подбирается перебором.
JWT_SECRET_MIN_LENGTH = 32

# override=False: переменные окружения не перетираются значениями из .env.
load_dotenv(dotenv_path=ENV_FILE, override=False)


class Settings(BaseSettings):
    """Настройки приложения из окружения и .env."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # Чтобы JWT_SECRET не попал в текст ValidationError.
        hide_input_in_errors=True,
    )

    # repr=False: в строке подключения пароль БД.
    database_url: str = Field(repr=False)

    # Без значения по умолчанию: запасной ключ в коде позволил бы подделать токен.
    # Генерируется setup_jwt.py.
    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 15
    jwt_issuer: str = "sneaker-practice"
    jwt_audience: str = "sneaker-api"

    @field_validator("jwt_secret")
    @classmethod
    def _check_secret_length(cls, value: SecretStr) -> SecretStr:
        """Требовать ключ не короче JWT_SECRET_MIN_LENGTH."""
        if len(value.get_secret_value()) < JWT_SECRET_MIN_LENGTH:
            raise ValueError(
                f"JWT_SECRET must be at least {JWT_SECRET_MIN_LENGTH} characters; "
                "generate one with `python setup_jwt.py`"
            )
        return value

    @field_validator("jwt_algorithm")
    @classmethod
    def _check_algorithm(cls, value: str) -> str:
        """Разрешить только HS256."""
        if value != "HS256":
            raise ValueError("JWT_ALGORITHM must be HS256 for this practice project")
        return value


settings = Settings()
