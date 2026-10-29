"""
app/config.py — центральные настройки приложения.

Источник: .env (единственный). Если .env нет — сервис падает на старте
с понятной ошибкой. 

Использование:
    from app.config import get_settings
    settings = get_settings()
    print(settings.db_url)       # postgresql://...
    print(settings.model_path)   # /path/to/models/xgboost_honest_v1.pkl

⚠️ При старте валидирует типы (int, bool). Если в .env кривое значение —
упадёт с ValidationError сразу, а не через 5 запросов.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


# ============================================================
# КОРЕНЬ ПРОЕКТА
# ============================================================
# __file__ = .../fastapi_app/app/config.py
# .parent       = .../fastapi_app/app/
# .parent.parent= .../fastapi_app/
# Работает и локально, и в Docker (WORKDIR /app).
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"


# ============================================================
# НАСТРОЙКИ
# ============================================================
class Settings(BaseSettings):
    """
    Все настройки сервиса.

    Поля можно переопределить через .env или переменные окружения.
    Имена полей — snake_case, в .env — UPPER_SNAKE (case_sensitive=False).
    """

    # ---------- Приложение ----------
    app_name: str = "Drug Deficit Predictor"
    app_version: str = "1.0.0"
    debug: bool = False
    log_level: str = "INFO"

    # ---------- PostgreSQL ----------
    # Без дефолтов для db_host/db_user/db_password/db_name —
    # .env ОБЯЗАТЕЛЕН. Если чего-то не хватает — ValidationError на старте.
    db_host: str
    db_port: int 
    db_user: str
    db_password: str
    db_name: str

    # Пул соединений (см. app/core/db.py на Шаге 3)
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout: int = 30        # секунды
    db_pool_recycle: int = 1800      # 30 минут

    # ---------- Пути к моделям ----------
    model_filename: str = "xgboost_honest_v1.pkl"
    ohe_filename: str = "onehot_encoder_v1.pkl"
    metadata_filename: str = "metadata_v1.json"
    models_dir: str = "models"

    # ---------- CORS ----------
    cors_origins: str = "*"

    # ---------- pydantic-settings ----------
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE) if ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ========================================================
    # COMPUTED ПОЛЯ (собираются из других, не читаются из .env)
    # ========================================================

    @computed_field  # type: ignore[prop-decorator]
    @property
    def db_url(self) -> str:
        """SQLAlchemy-URL для PostgreSQL."""
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def db_url_safe(self) -> str:
        """URL без пароля — для логов."""
        return (
            f"postgresql://{self.db_user}:***"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def models_path(self) -> Path:
        return BASE_DIR / self.models_dir

    @computed_field  # type: ignore[prop-decorator]
    @property
    def model_path(self) -> Path:
        return self.models_path / self.model_filename

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ohe_path(self) -> Path:
        return self.models_path / self.ohe_filename

    @computed_field  # type: ignore[prop-decorator]
    @property
    def metadata_path(self) -> Path:
        return self.models_path / self.metadata_filename

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origins_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]


# ============================================================
# СИНГЛТОН
# ============================================================

_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """
    Ленивая инициализация синглтона.
    Если .env отсутствует — падаем с понятной ошибкой.
    """
    global _settings
    if _settings is None:
        if not ENV_FILE.exists():
            raise RuntimeError(
                f"❌ Файл .env не найден: {ENV_FILE}\n"
                f"   Создай его из шаблона:\n"
                f"     copy .env.example .env     (Windows)\n"
                f"     cp .env.example .env       (Linux/macOS)\n"
                f"   и заполни DB_HOST, DB_USER, DB_PASSWORD, DB_NAME."
            )
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Для тестов: сбросить синглтон, чтобы перечитать .env."""
    global _settings
    _settings = None