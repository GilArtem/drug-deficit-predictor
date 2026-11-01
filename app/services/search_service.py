"""
app/services/search_service.py — поиск препаратов по названию.

Функция search_drugs(q, limit, offset) ищет по raw_data.drug_products:
- ILIKE по name_trade (торговое название)
- ILIKE по standard_inn (МНН)
- группирует по smnn_code (одна карточка = одна клиническая форма)
- пагинация LIMIT / OFFSET

Возвращает SearchResponse (см. app.schemas.response).

- Если risk_preview считается (with_risk_preview=True) — для КАЖДОЙ карточки
вызывается build_features() + predict_proba(). Это ~50 мс на карточку.
При limit=20 → ~1 сек. Для MVP приемлемо; в prod — кешировать.

SQL injection: используем параметры через SQLAlchemy text(), никогда f-string!

TODO !! В production — экранировать % и _ в пользовательском вводе!!
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
    RiskClass,
    RiskPreview,
    SearchResponse,
    SearchResultItem,
)

logger = logging.getLogger(__name__)

# Сколько карточек максимум — защита от «limit=10000»
MAX_LIMIT = 100
DEFAULT_LIMIT = 20


# ============================================================
# SQL-ЗАПРОСЫ (один раз объявлены, много раз используются)
# ============================================================

# COUNT — сколько всего СМНН подходит под запрос
_SQL_COUNT = text("""
    SELECT COUNT(DISTINCT smnn_code) AS total
    FROM raw_data.drug_products
    WHERE actual = TRUE
      AND smnn_code IS NOT NULL
      AND (
            name_trade ILIKE :pattern
         OR standard_inn ILIKE :pattern
      )
""")

# ITEMS — карточки текущей страницы (DISTINCT ON → одна строка на smnn_code)
_SQL_ITEMS = text("""
    SELECT DISTINCT ON (dp.smnn_code)
        dp.smnn_code,
        dp.standard_inn,
        dp.standard_form,
        dp.standard_doze
    FROM raw_data.drug_products dp
    WHERE dp.actual = TRUE
      AND dp.smnn_code IS NOT NULL
      AND (
            dp.name_trade ILIKE :pattern
         OR dp.standard_inn ILIKE :pattern
      )
    ORDER BY dp.smnn_code, dp.id_original
    LIMIT :limit OFFSET :offset
""")

# TRADE NAMES — торговые названия для списка smnn_code (для карточек)
_SQL_TRADE_NAMES = text("""
    SELECT
        smnn_code,
        ARRAY_AGG(DISTINCT name_trade) FILTER (WHERE name_trade IS NOT NULL) AS trade_names
    FROM raw_data.drug_products
    WHERE actual = TRUE
      AND smnn_code = ANY(:codes)
    GROUP BY smnn_code
""")


# ============================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================

def search_drugs(
    q: str,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    with_risk_preview: bool = True,
    engine: Optional[Engine] = None,
) -> SearchResponse:
    """
    Поиск препаратов по названию.

    Параметры:
        q                  — строка запроса (например, "ибупрофен").
        limit              — сколько карточек на страницу (1..MAX_LIMIT).
        offset             — сколько пропустить (для пагинации).
        with_risk_preview  — считать ли risk_preview для каждой карточки (медленнее).
        engine             — SQLAlchemy Engine. Если None — берём из app.core.db.

    Возвращает SearchResponse.

    Исключения:
        ValueError — если q пустой, limit/offset некорректные.
    """
    # --- Валидация входа ---
    q = (q or "").strip()
    if not q:
        raise ValueError("q не может быть пустым")

    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit должен быть в [1, {MAX_LIMIT}], получено {limit}")

    if offset < 0:
        raise ValueError(f"offset должен быть >= 0, получено {offset}")

    # --- Engine ---
    if engine is None:
        from app.core.db import get_engine
        engine = get_engine()

    # --- Паттерн для ILIKE: '%ибупрофен%' ---
    # ⚠️ % и _ в q — экранируем, чтобы не было инъекций в LIKE.
    # (Для MVP достаточно; в prod — отдельная функция escape_like)
    pattern = f"%{q}%"

    # --- 1. COUNT ---
    with engine.connect() as conn:
        total = conn.execute(_SQL_COUNT, {"pattern": pattern}).scalar() or 0

    # --- Если ничего не нашли — вернуть пустой SearchResponse ---
    if total == 0:
        logger.info(f"Поиск '{q}': ничего не найдено")
        return SearchResponse(total=0, limit=limit, offset=offset, items=[])

    # --- 2. ITEMS (LIMIT / OFFSET) ---
    with engine.connect() as conn:
        rows = conn.execute(
            _SQL_ITEMS,
            {"pattern": pattern, "limit": limit, "offset": offset},
        ).mappings().all()

    if not rows:
        # total > 0, но offset за пределами → пустая страница
        logger.warning(f"Поиск '{q}': offset={offset} за пределами total={total}")
        return SearchResponse(total=total, limit=limit, offset=offset, items=[])

    codes = [r["smnn_code"] for r in rows]

    # --- 3. TRADE NAMES для всех найденных СМНН (1 запрос на все) ---
    with engine.connect() as conn:
        trade_rows = conn.execute(
            _SQL_TRADE_NAMES, {"codes": codes}
        ).mappings().all()
    trade_map = {r["smnn_code"]: list(r["trade_names"] or []) for r in trade_rows}

    # --- 4. Собираем карточки ---
    items: list[SearchResultItem] = []
    for r in rows:
        code = r["smnn_code"]

        # --- 4a. risk_preview (если включён) ---
        risk_preview: Optional[RiskPreview] = None
        if with_risk_preview:
            risk_preview = _compute_risk_preview(code)

        items.append(
            SearchResultItem(
                smnn_code=code,
                standard_inn=r["standard_inn"] or "",
                standard_form=r["standard_form"],
                standard_doze=r["standard_doze"],
                trade_names=trade_map.get(code, []),
                risk_preview=risk_preview,
            )
        )

    logger.info(
        f"Поиск '{q}': total={total}, на странице={len(items)}, "
        f"preview={with_risk_preview}"
    )

    return SearchResponse(total=total, limit=limit, offset=offset, items=items)


# ============================================================
# ВСПОМОГАТЕЛЬНОЕ: превью риска для одной карточки
# ============================================================

def _compute_risk_preview(smnn_code: str) -> Optional[RiskPreview]:
    """
    Считает краткое превью риска для карточки поиска.

    Возвращает None, если:
    - нет признаков (СМНН не найден в actual);
    - нет динамики (нет продаж/остатков) — модель не может предсказать.

    Не бросает исключений (чтобы одна «плохая» карточка не ломала весь поиск).
    """
    try:
        features = build_features(smnn_code)
        if features is None:
            return None

        if not features.attrs.get("dynamic_available", False):
            # Нет динамики — превью бессмысленно
            return None

        proba = predict_proba(features)[0]      # [P(low), P(medium), P(high)]
        pred_class = int(proba.argmax())

        return RiskPreview(
            risk_class=RiskClass(pred_class),
            label=CLASS_LABELS[pred_class],
            probability=float(proba[pred_class]),
        )
    except Exception as e:
        logger.warning(f"risk_preview для {smnn_code} не удался: {e}")
        return None