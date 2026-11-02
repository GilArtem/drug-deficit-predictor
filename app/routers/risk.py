"""
app/routers/risk.py — эндпоинт /api/risk/{smnn_code}.

Тонкий слой: получает smnn_code → вызывает get_risk → RiskResponse или 404.
Вся логика — в app.services.risk_service.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Path

from app.schemas.response import ErrorResponse, RiskResponse
from app.services.risk_service import get_risk

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["risk"])


@router.get(
    "/risk/{smnn_code}",
    response_model=RiskResponse,
    summary="Прогноз риска дефицита для одного СМНН",
    description=(
        "Возвращает полный прогноз: класс риска (0/1/2), вероятности, "
        "ключевые признаки и рекомендации. "
        "Если для СМНН нет данных о продажах/остатках — risk=null, "
        "dynamic_data_available=false, но остальные поля заполнены."
    ),
    responses={
        404: {
            "model": ErrorResponse,
            "description": "СМНН не найден в реестре ЕСКЛП",
        },
    },
)
def risk(
    smnn_code: str = Path(
        ...,
        min_length=1,
        max_length=64,
        description="Код СМНН (например, '21.10.51.121-000001-1-00120-0000000000000')",
    ),
) -> RiskResponse:
    """
    Прогноз риска для одного СМНН.

    Пример:
        GET /api/risk/21.10.51.121-000001-1-00120-0000000000000
    """
    logger.info(f"GET /api/risk/{smnn_code}")

    result = get_risk(smnn_code)
    if result is None:
        logger.warning(f"СМНН не найден: {smnn_code}")
        # Возвращаем ErrorResponse (схема Шага 6) через HTTPException
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "detail": f"СМНН {smnn_code} не найден в реестре ЕСКЛП",
                "code": 404,
            },
        )

    return result