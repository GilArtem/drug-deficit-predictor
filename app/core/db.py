"""
app/core/db.py — подключение к PostgreSQL через SQLAlchemy.

Содержит:
- engine: один на приложение (пул соединений)
- SessionLocal: фабрика сессий (короткоживущие, на запрос)
- get_db(): FastAPI dependency — выдаёт сессию роутеру и закрывает после
- check_db_health(): для /api/health — проверка "жива ли БД"

⚠️ Engine создаётся ОДИН раз при старте (см. main.py).
   Не вызывай create_db_engine() в каждом запросе — потеряешь пул.
"""

from __future__ import annotations

import logging
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)


# ============================================================
# ENGINE (создаётся лениво, один раз)
# ============================================================

_engine: Engine | None = None


def create_db_engine() -> Engine:
    """
    Создаёт Engine с пулом соединений.

    Параметры пула — из .env (db_pool_size и т.д.).
    ⚠️ Вызывается ОДИН раз при старте сервиса.
    """
    settings = get_settings()

    logger.info(f"Создаю Engine: {settings.db_url_safe}")
    logger.info(
        f"Пул: size={settings.db_pool_size}, "
        f"max_overflow={settings.db_max_overflow}, "
        f"timeout={settings.db_pool_timeout}s, "
        f"recycle={settings.db_pool_recycle}s"
    )

    return create_engine(
        settings.db_url,
        # --- Пул ---
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        # --- Защита от "мёртвых" соединений ---
        pool_pre_ping=True,
        # --- Логирование SQL (только при DEBUG=true) ---
        echo=settings.debug,
        # --- Кодировка (на случай не-UTF8 данных) ---
        connect_args={"client_encoding": "utf8"},
    )


def get_engine() -> Engine:
    """
    Возвращает engine (создаёт при первом вызове — лениво).
    Полезно для тестов, чтобы избежать глобального состояния при импорте.
    """
    global _engine
    if _engine is None:
        _engine = create_db_engine()
    return _engine


def dispose_engine() -> None:
    """Закрыть пул (для graceful shutdown и тестов)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None
        logger.info("Engine закрыт (dispose)")


# ============================================================
# SESSION FACTORY
# ============================================================

def _make_session_factory() -> sessionmaker[Session]:
    """
    Создаёт фабрику сессий, привязанную к текущему engine.

    ⚠️ Не создавай SessionLocal на уровне модуля — тогда engine создастся
    при импорте (до инициализации логов, до .env — в тестах неудобно).
    Поэтому — ленивая фабрика.
    """
    return sessionmaker(
        bind=get_engine(),
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,   # объекты остаются доступны после commit()
    )


_SessionLocal: sessionmaker[Session] | None = None


def get_session_factory() -> sessionmaker[Session]:
    """Ленивый доступ к фабрике сессий."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = _make_session_factory()
    return _SessionLocal


# ============================================================
# FASTAPI DEPENDENCY
# ============================================================

def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency: выдаёт сессию на время запроса и закрывает её после.

    Использование в роутере:
        @router.get("/...")
        def endpoint(db: Session = Depends(get_db)):
            ...

    Гарантии:
    - Сессия закрывается ВСЕГДА (успех, ошибка, HTTPException) — благодаря try/finally.
    - Для read-only запросов commit() не нужен — просто SELECT.
    """
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================================================
# HEALTH CHECK
# ============================================================

def check_db_health() -> dict:
    """
    Проверка "жива ли БД" для /api/health.

    Возвращает:
        {
            "status": "ok" | "error",
            "database": "<имя>",
            "server_version": "PostgreSQL 17.x ...",
            "latency_ms": 1.23,
            "error": None | "<сообщение>"
        }
    """
    import time

    settings = get_settings()
    start = time.perf_counter()

    try:
        engine = get_engine()
        with engine.connect() as conn:
            # SELECT 1 — минимальная проверка, что соединение живо.
            conn.execute(text("SELECT 1"))
            # Версия PostgreSQL — полезно видеть в /health.
            version = conn.execute(text("SHOW server_version")).scalar()

        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return {
            "status": "ok",
            "database": settings.db_name,
            "server_version": str(version),
            "latency_ms": latency_ms,
            "error": None,
        }
    except Exception as e:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.error(f"DB health check failed: {e}")
        return {
            "status": "error",
            "database": settings.db_name,
            "server_version": None,
            "latency_ms": latency_ms,
            "error": str(e),
        }