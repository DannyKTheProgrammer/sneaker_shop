# Конфигурация приложения.
#
# Задача файла — один раз прочитать окружение и отдать приложению
# неизменяемый объект настроек. Два источника, порядок важен:
#   1) уже заданные переменные окружения процесса (они главнее);
#   2) файл .env в корне проекта (через python-dotenv).
#
# .env ищется от расположения этого файла, а не от текущей рабочей папки:
#   app/core/config.py -> parents[2] == корень проекта,
# поэтому `python -m uvicorn app.main:app` работает и из корня, и из другого cwd.

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень проекта: .../sneaker_shop (на два уровня выше app/core/).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

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
    )

    # Обязательное поле: если DATABASE_URL ни в окружении, ни в .env,
    # приложение падает при импорте с явной ошибкой, а не молча
    # пытается подключиться "куда-нибудь".
    database_url: str


settings = Settings()
