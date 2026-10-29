# Конфигурация приложения.
#
# Задача файла — один раз прочитать окружение и отдать приложению
# неизменяемый объект настроек. Два источника, порядок важен:
#   1) уже заданные переменные окружения процесса (они главнее);
#   2) файл .env в корне проекта (через python-dotenv).
#
# .env ищется от расположения этого файла, а не от текущей рабочей папки:
#   app/core/config.py -> parents[2] == корень проекта,
# поэтому `python -m uvicorn app.final_main:app` работает и из корня, и из другого cwd.

from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень проекта: .../sneaker_shop (на два уровня выше app/core/).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

# Минимальная длина секрета подписи. HS256 на коротком ключе подбирается
# перебором, поэтому требуем не меньше 32 символов: setup_jwt.py генерирует
# token_urlsafe(48) — это 64 символа, запас есть.
JWT_SECRET_MIN_LENGTH = 32

# override=False — ключевой момент: переменные, которые уже есть в окружении
# (например, экспортированные в терминале или заданные в CI/Docker),
# НЕ перетираются значениями из .env. Файл лишь дополняет окружение.
load_dotenv(dotenv_path=ENV_FILE, override=False)


class Settings(BaseSettings):
    """Настройки приложения, прочитанные из окружения после загрузки .env."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # Не печатать присланные значения в тексте ValidationError: иначе при
        # ошибке конфигурации (короткий ключ, нет DATABASE_URL) в консоль и
        # логи попадает сам JWT_SECRET или его начало и конец (input_value).
        hide_input_in_errors=True,
    )

    # Обязательное поле: если DATABASE_URL ни в окружении, ни в .env,
    # приложение падает при импорте с явной ошибкой, а не молча
    # пытается подключиться "куда-нибудь".
    #
    # repr=False: в строке подключения есть пароль БД, поэтому она не
    # попадает в repr(settings) / print(settings) и в логи, куда объект
    # настроек могут случайно вывести. Тип остаётся str: Engine, Alembic и
    # вспомогательные скрипты читают settings.database_url как обычно.
    database_url: str = Field(repr=False)

    # Секрет подписи JWT. Тоже обязателен и БЕЗ значения по умолчанию:
    # запасной ключ в коде означал бы, что любой, кто читал исходники,
    # может подписать произвольный токен. Выдаётся скриптом setup_jwt.py.
    #
    # SecretStr, а не str: значение не попадает ни в repr настроек, ни в
    # логи, ни в текст ошибки валидации — его можно получить только явным
    # вызовом get_secret_value().
    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 15
    jwt_issuer: str = "sneaker-practice"
    jwt_audience: str = "sneaker-api"

    @field_validator("jwt_secret")
    @classmethod
    def _check_secret_length(cls, value: SecretStr) -> SecretStr:
        """Слишком короткий ключ — ошибка конфигурации, а не «мелочь»."""
        if len(value.get_secret_value()) < JWT_SECRET_MIN_LENGTH:
            raise ValueError(
                f"JWT_SECRET must be at least {JWT_SECRET_MIN_LENGTH} characters; "
                "generate one with `python setup_jwt.py`"
            )
        return value

    @field_validator("jwt_algorithm")
    @classmethod
    def _check_algorithm(cls, value: str) -> str:
        """Разрешаем только HS256: алгоритм подписи не должен приходить извне."""
        if value != "HS256":
            raise ValueError("JWT_ALGORITHM must be HS256 for this practice project")
        return value


settings = Settings()
