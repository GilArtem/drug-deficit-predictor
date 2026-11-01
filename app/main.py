"""
app/main.py — точка входа FastAPI-сервиса прогнозирования дефицита ЛС.

Запуск:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

Что делает при старте (lifespan):
1. get_engine()           — создаёт пул соединений с PostgreSQL.
2. init_feature_builder() — загружает OneHotEncoder, справочники, кеши (~5 SQL).
3. get_model()            — загружает XGB-honest .pkl.

Если что-то упало — сервис НЕ стартует (fail-fast). Это правильно:
лучше упасть при старте, чем отдавать 500 на каждый запрос.

Эндпоинты (MVP):
- GET /api/health          — health-check (БД + модель + FeatureBuilder)
- GET /                     — простой «сервис работает» 
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import get_settings
from app.core.db import check_db_health, dispose_engine, get_engine
from app.core.feature_builder import get_builder, init_feature_builder
from app.core.model_loader import check_model_health, get_model


# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================
# Настраиваем ДО создания app, чтобы логи FastAPI/uvicorn тоже шли через наш формат.
# basicConfig — идемпотентен: повторный вызов не перезапишет существующие handlers.

settings = get_settings()
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("app.main")


# ============================================================
# LIFESPAN — startup + shutdown
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Инициализация при старте и cleanup при shutdown.

    Порядок важен:
        1. engine          (нужен для feature_builder)
        2. feature_builder (нужен для risk_service)
        3. model           (нужен для risk_service)

    Если что-то падает — uvicorn не запустится.
    """
    logger.info("=" * 60)
    logger.info(f"🚀 Запуск {settings.app_name} v{settings.app_version}")
    logger.info(f" DB: {settings.db_url_safe}")
    logger.info("=" * 60)

    # --- 1. Engine ---
    logger.info("[startup 1/3] Инициализация Engine...")
    engine = get_engine()

    # --- 2. FeatureBuilder ---
    logger.info("[startup 2/3] Инициализация FeatureBuilder...")
    init_feature_builder(
        db_engine=engine,
        ohe_path=str(settings.ohe_path),
        metadata_path=str(settings.metadata_path),
    )

    # --- 3. Model ---
    logger.info("[startup 3/3] Загрузка модели...")
    get_model()

    logger.info("=" * 60)
    logger.info("✅ Сервис готов к работе")
    logger.info("=" * 60)

    # --- Работаем ---
    yield

    # --- Shutdown ---
    logger.info("🛑 Остановка сервиса...")
    dispose_engine()
    logger.info("✅ Сервис остановлен")


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Прогнозирование риска дефицита лекарственных средств. "
        "Модель: XGB-honest."
    ),
    debug=settings.debug,
    lifespan=lifespan,
)

# --- CORS ---
# Для MVP: разрешаем всё (CORS_ORIGINS=*). В prod — конкретный домен.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# ЭНДПОИНТЫ
# ============================================================

@app.get("/api/health", tags=["system"], summary="Health-check")
def health(request: Request) -> JSONResponse:
    """
    Сводная проверка: БД + модель + FeatureBuilder.

    Возвращает:
    - 200 OK — (`status: "ok"`).
    - 503 Service Unavailable — (`status: "degraded"` или `"error"`).

    Используется load-balancer'ом / k8s для решения «отправлять трафик?».
    """
    db_status = check_db_health()
    model_status = check_model_health()

    # FeatureBuilder — синглтон. Если не инициализирован, get_builder() бросит RuntimeError.
    try:
        builder = get_builder()
        fb_status = {
            "status": "ok",
            "n_features": len(builder.honest_features),
            "atc_groups_loaded": len(builder.atc_level4_counts),
            "error": None,
        }
    except Exception as e:
        fb_status = {"status": "error", "n_features": None, "error": str(e)}

    components = {
        "database": db_status,
        "model": model_status,
        "feature_builder": fb_status,
    }

    # Определяем общий статус:
    # - "ok"       — все три ok
    # - "degraded" — есть ok, но не все (часть работает)
    # - "error"    — ни один не ok
    statuses = [c["status"] for c in components.values()]
    if all(s == "ok" for s in statuses):
        overall = "ok"
        http_code = 200
    elif any(s == "ok" for s in statuses):
        overall = "degraded"
        http_code = 503
    else:
        overall = "error"
        http_code = 503

    return JSONResponse(
        status_code=http_code,
        content={
            "status": overall,
            "app": settings.app_name,
            "version": settings.app_version,
            "components": components,
        },
    )


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root() -> str:
    """
    Заглушка главной страницы. Далее заменим на реальный UI.
    """
    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="utf-8">
        <title>{settings.app_name}</title>
        <style>
            body {{ font-family: system-ui, sans-serif; max-width: 720px; margin: 60px auto; padding: 0 20px; }}
            h1 {{ color: #1e3a8a; }}
            code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 4px; }}
            a {{ color: #2563eb; }}
        </style>
    </head>
    <body>
        <h1>💊 {settings.app_name}</h1>
        <p>Версия <code>{settings.app_version}</code> — сервис работает.</p>
        <p>
            <a href="/api/health">/api/health</a> — проверка статуса<br>
            <a href="/docs">/docs</a> — Swagger UI (интерактивная документация)
        </p>
        <p><em>UI появится позже.</em></p>
    </body>
    </html>
    """