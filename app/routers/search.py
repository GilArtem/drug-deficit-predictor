"""
app/routers/search.py — эндпоинт /api/search.

Тонкий слой: валидация параметров → вызов search_drugs → ответ SearchResponse.
Вся логика — в app.services.search_service.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from app.schemas.response import SearchResponse
from app.services.search_service import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    search_drugs,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["search"])


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="Поиск препаратов по названию",
    description=(
        "Ищет препараты в реестре ЕСКЛП по торговому названию или МНН (регистронезависимо). "
        "Возвращает список СМНН с кратким превью риска."
    ),
    responses={
        422: {"description": "Невалидные параметры (q пустой, limit вне диапазона)"},
    },
)
def search(
    q: str = Query(
        ...,
        min_length=1,
        max_length=200,
        description="Строка поиска: торговое название или МНН (например, 'ибупрофен')",
        examples=["ибупрофен"],
    ),
    limit: int = Query(
        DEFAULT_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description=f"Сколько карточек на страницу (1..{MAX_LIMIT})",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="Сколько пропустить (для пагинации)",
    ),
) -> SearchResponse:
    """
    Поиск препаратов.

    Пример:
        GET /api/search?q=ибупрофен&limit=10&offset=0
    """
    logger.info(f"GET /api/search q={q!r} limit={limit} offset={offset}")
    return search_drugs(q=q, limit=limit, offset=offset)