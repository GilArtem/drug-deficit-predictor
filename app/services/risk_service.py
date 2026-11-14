"""
app/services/risk_service.py — полный прогноз риска для одного СМНН.

Функция get_risk(smnn_code) возвращает RiskResponse (см. schemas.response):
- risk:              RiskInfo | None
- features_snapshot: ключевые признаки (прозрачность прогноза)
- dynamic_data_available: bool (есть ли данные для прогноза)
- recommendations:   list[str]

Сценарии:
- СМНН не найден                  → return None (роутер вернёт 404)
- найден, но нет динамики         → risk=None, dynamic_data_available=False + пояснение
- найден, динамика есть           → полный RiskInfo + рекомендации
- ошибка при predict_proba        → risk=None, error_message (не падаем)

- Идемпотентно: get_risk не меняет БД, только читает.
- Стоимость: build_features() ~5 SQL + predict_proba ~50 мс.
  
TODO  !!Для MVP — ок; в prod — кеш на 1 час (Шаг 11).!!
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine
from app.core.feature_builder import build_features
from app.core.model_loader import CLASS_LABELS, predict_proba
from app.schemas.response import (
    DaysOfStock,
    FeaturesSnapshot,
    Producer,
    RiskClass,
    RiskInfo,
    RiskProbabilities,
    RiskResponse,
)
from app.services.recommendation import generate_recommendations 

logger = logging.getLogger(__name__)


# ============================================================
# SQL: статика по СМНН (МНН, форма, дозировка, торговые названия)
# ============================================================

_SQL_STATIC = text("""
    SELECT
        dp.standard_inn,
        dp.standard_form,
        dp.standard_doze,
        ARRAY_AGG(DISTINCT dp.name_trade)
            FILTER (WHERE dp.name_trade IS NOT NULL) AS trade_names
    FROM raw_data.drug_products dp
    WHERE dp.smnn_code = :smnn_code
      AND dp.actual = TRUE
    GROUP BY dp.smnn_code, dp.standard_inn, dp.standard_form, dp.standard_doze
""")

# ============================================================
# SQL: производители (уникальные) для СМНН
# ============================================================

_SQL_PRODUCERS = text("""
    SELECT DISTINCT
        dp.name_producer AS name,
        dp.country AS country
    FROM raw_data.drug_products dp
    WHERE dp.smnn_code = :smnn_code
      AND dp.actual = TRUE
      AND dp.name_producer IS NOT NULL
    ORDER BY dp.name_producer
""")


# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================

def get_risk(
    smnn_code: str,
    engine: Optional[Engine] = None,
) -> Optional[RiskResponse]:
    """
    Полный прогноз риска для одного СМНН.

    Возвращает:
        RiskResponse — если СМНН найден (с risk или без, но с features_snapshot).
        None         — если СМНН не найден в actual drug_products → роутер вернёт 404.

    Исключения: нет (внутри обрабатываются и логируются).
    """
    if not smnn_code or not smnn_code.strip():
        return None

    smnn_code = smnn_code.strip()

    if engine is None:
        from app.core.db import get_engine
        engine = get_engine()

    # --- 1. Статика: МНН, форма, дозировка, торговые названия ---
    with engine.connect() as conn:
        row = conn.execute(_SQL_STATIC, {"smnn_code": smnn_code}).mappings().first()

    if row is None:
        logger.info(f"СМНН не найден: {smnn_code}")
        return None

    standard_inn = row["standard_inn"] or ""
    standard_form = row["standard_form"]
    standard_doze = row["standard_doze"]
    trade_names = list(row["trade_names"] or [])

    # --- 1.5. Производители ---
    with engine.connect() as conn:
        producers_rows = conn.execute(
            _SQL_PRODUCERS, {"smnn_code": smnn_code}
        ).mappings().all()
    producers = [
        Producer(name=r["name"], country=r["country"])
        for r in producers_rows
    ]

    # --- 2. Признаки (74 числа) ---
    features = build_features(smnn_code)
    if features is None:
        # На практике п.1 уже вернул None, но на всякий случай
        logger.warning(f"build_features вернул None для {smnn_code}")
        return None

    # --- 3. Снимок признаков (всегда, независимо от динамики) ---
    snapshot = _make_snapshot(features)

    # --- 4. Есть ли динамика? ---
    dynamic_available = bool(features.attrs.get("dynamic_available", False))

    if not dynamic_available:
        # Нет продаж/остатков → модель не может предсказать
        logger.info(f"Нет динамики для {smnn_code} — риск не считается")
        return RiskResponse(
            smnn_code=smnn_code,
            standard_inn=standard_inn,
            standard_form=standard_form,
            standard_doze=standard_doze,
            trade_names=trade_names,
            risk=None,
            features_snapshot=snapshot,
            dynamic_data_available=False,
            recommendations=[
                "Недостаточно данных о продажах/остатках для прогноза.",
                "Проверьте актуальность данных в системе учёта.",
            ],
            producers=producers,                                         
            days_of_stock=_compute_days_of_stock(features, False), 
        )

    # --- 5. Прогноз ---
    try:
        proba = predict_proba(features)[0]         # [P_low, P_med, P_high]
        pred_class = int(proba.argmax())

        risk_info = RiskInfo(
            risk_class=RiskClass(pred_class),
            label=CLASS_LABELS[pred_class],
            probability=float(proba[pred_class]),
            probabilities=RiskProbabilities(
                low=float(proba[0]),
                medium=float(proba[1]),
                high=float(proba[2]),
            ),
        )

        # --- 6. Рекомендации (Шаг 9 — заглушка; заменим на генератор) ---
        # recommendations = _placeholder_recommendations(
        #     pred_class=pred_class, snapshot=snapshot
        # )
        recommendations = generate_recommendations(pred_class, snapshot)

        logger.info(
            f"Прогноз {smnn_code}: class={pred_class} ({CLASS_LABELS[pred_class]}), "
            f"p={proba[pred_class]:.3f}"
        )

        return RiskResponse(
            smnn_code=smnn_code,
            standard_inn=standard_inn,
            standard_form=standard_form,
            standard_doze=standard_doze,
            trade_names=trade_names,
            risk=risk_info,
            features_snapshot=snapshot,
            dynamic_data_available=True,
            recommendations=recommendations,
            producers=producers,                                         
            days_of_stock=_compute_days_of_stock(features, True),
        )

    except Exception as e:
        logger.error(f"predict_proba упал для {smnn_code}: {e}")
        return RiskResponse(
            smnn_code=smnn_code,
            standard_inn=standard_inn,
            standard_form=standard_form,
            standard_doze=standard_doze,
            trade_names=trade_names,
            risk=None,
            features_snapshot=snapshot,
            dynamic_data_available=True,
            recommendations=[
                f"Ошибка при расчёте прогноза. Повторите позже.",
            ],
            producers=producers,                                         
            days_of_stock=_compute_days_of_stock(features, True),
        )


# ============================================================
# СНИМОК ПРИЗНАКОВ (FeaturesSnapshot)
# ============================================================

def _make_snapshot(features: pd.DataFrame) -> FeaturesSnapshot:
    """Извлекает ключевые признаки для features_snapshot."""
    row = features.iloc[0]
    return FeaturesSnapshot(
        products_count=int(row["products_count"]),
        producers_count=int(row["producers_count"]),
        is_essential=bool(row["is_essential"]),
        is_narcotic=bool(row["is_narcotic"]),
        has_atc_substitutes=bool(row["has_atc_substitutes"]),
        avg_age=float(row["avg_age"]),
    )

# ============================================================
# Расчет дней запаса
# ============================================================

def _compute_days_of_stock(features: pd.DataFrame, dynamic_available: bool) -> DaysOfStock:
    """
    Считает, сколько дней хватит текущего запаса.
    
    Формула:
        daily_sales = sales_last_month / 30
        days_of_stock = current_stock / daily_sales
    """
    if not dynamic_available:
        return DaysOfStock(
            days=None,
            current_stock=None,
            daily_sales=None,
            fallback_message=(
                "Недостаточно данных о продажах/остатках. "
                "Прогноз основан только на статических признаках."
            ),
        )
    
    row = features.iloc[0]
    current_stock = int(row.get('current_stock', 0))
    sales_last_month = int(row.get('sales_last_month', 0))
    
    if sales_last_month <= 0:
        return DaysOfStock(
            days=None,
            current_stock=current_stock,
            daily_sales=None,
            fallback_message="Нет данных о продажах — невозможно рассчитать дни запаса.",
        )
    
    daily_sales = sales_last_month / 30.0
    days = current_stock / daily_sales
    
    return DaysOfStock(
        days=round(days, 1),
        current_stock=current_stock,
        daily_sales=round(daily_sales, 1),
    )


# # ============================================================
# # РЕКОМЕНДАЦИИ — ЗАГЛУШКА (заменим на Шаге 9)
# # ============================================================
# def _placeholder_recommendations(pred_class: int, snapshot: FeaturesSnapshot) -> list[str]:
#     """
#     Простые рекомендации по классу риска.
#     Шаг 9 заменит на полноценный recommendation.py.
#     """
#     if pred_class == 2:  # высокий
#         recs = [
#             "Увеличить страховой запас (рекомендуется ×1.5 от текущего).",
#             "Связаться с альтернативными поставщиками.",
#         ]
#         if snapshot.has_atc_substitutes:
#             recs.append("Рассмотреть аналоги по ATC-группе.")
#         if snapshot.is_essential:
#             recs.append("⚠️ ЖНВЛП — критичный препарат, эскалировать в закупки.")
#     elif pred_class == 1:  # средний
#         recs = [
#             "Мониторить остатки еженедельно.",
#             "Пересмотреть нормативы страхового запаса.",
#         ]
#         if snapshot.is_essential:
#             recs.append("ЖНВЛП — приоритетный контроль.")
#     else:  # низкий
#         recs = [
#             "Поддерживать текущий уровень запаса.",
#             "Плановый пересмотр — через квартал.",
#         ]
#     return recs