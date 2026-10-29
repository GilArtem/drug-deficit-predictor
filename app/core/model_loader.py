"""
app/core/model_loader.py — загрузка ML-модели.

Содержит:
- get_model():           синглтон XGBClassifier (загружается один раз)
- get_metadata():        metadata_v1.json (кэш)
- predict_proba(features): удобная обёртка над model.predict_proba
- check_model_health():  для /api/health

⚠️ Модель загружается ОДИН раз при старте (см. main.py).
   Не вызывай joblib.load() в каждом запросе — это ~200–500 мс.

Контракт с моделью:
- n_features_in_ == 74 (после вычета PROXIES)
- classes_ == [0, 1, 2] (0=низкий, 1=средний, 2=высокий)
- target = deficit_risk (см. metadata_v1.json)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import joblib
import numpy as np
import pandas as pd

from app.config import get_settings

logger = logging.getLogger(__name__)

# Контракт с моделью (см. metadata_v1.json['features'] минус PROXIES)
PROXIES = ["current_stock_days", "stock_to_sales_ratio"]
EXPECTED_N_FEATURES_HONEST = 74
EXPECTED_CLASSES = [0, 1, 2]

# Человекочитаемые метки классов (для ответа API)
CLASS_LABELS = {0: "Низкий", 1: "Средний", 2: "Высокий"}


# ============================================================
# СИНГЛТОНЫ
# ============================================================

_model: Any = None            # xgboost.XGBClassifier (или Booster)
_metadata: Optional[dict] = None


def get_model():
    """
    Возвращает загруженную модель (синглтон).

    При первом вызове:
    - Загружает .pkl через joblib.
    - Проверяет контракт: n_features == 74, classes == [0,1,2].
    - Падает с понятной ошибкой, если контракт нарушен.

    ⚠️ Вызывается в startup (main.py). В роутерах — только из кэша.
    """
    global _model
    if _model is not None:
        return _model

    settings = get_settings()
    model_path = settings.model_path

    if not model_path.exists():
        raise RuntimeError(
            f"❌ Файл модели не найден: {model_path}\n"
            f"   Проверь MODELS_DIR и MODEL_FILENAME в .env."
        )

    logger.info(f"Загружаю модель: {model_path}")
    _model = joblib.load(str(model_path))

    # --- Проверка контракта ---
    n_features = getattr(_model, "n_features_in_", None)
    if n_features is not None and n_features != EXPECTED_N_FEATURES_HONEST:
        raise RuntimeError(
            f"❌ Модель ожидает {n_features} фич, а FeatureBuilder даёт "
            f"{EXPECTED_N_FEATURES_HONEST}. Проверь PROXIES и metadata."
        )

    classes = getattr(_model, "classes_", None)
    if classes is not None and list(classes) != EXPECTED_CLASSES:
        raise RuntimeError(
            f"❌ Модель обучена на классах {list(classes)}, "
            f"а ожидалось {EXPECTED_CLASSES}."
        )

    logger.info(
        f"✅ Модель загружена: {type(_model).__name__}, "
        f"n_features={n_features}, classes={list(classes) if classes is not None else '?'}"
    )
    return _model


def get_metadata() -> dict:
    """Возвращает metadata_v1.json (кэш)."""
    global _metadata
    if _metadata is not None:
        return _metadata

    settings = get_settings()
    metadata_path = settings.metadata_path
    if not metadata_path.exists():
        raise RuntimeError(f"❌ metadata не найден: {metadata_path}")

    with open(metadata_path, "r", encoding="utf-8") as f:
        _metadata = json.load(f)
    logger.info(f"✅ Metadata загружена: {metadata_path.name}")
    return _metadata


# ============================================================
# ПРЕДСКАЗАНИЕ
# ============================================================

def predict_proba(features: pd.DataFrame) -> np.ndarray:
    """
    Обёртка над model.predict_proba.

    features: DataFrame формы (n, 74) в порядке metadata['features'] минус PROXIES.
              Обычно 1 строка — признаки одного СМНН.

    Возвращает: np.ndarray (n, 3) — вероятности классов [низкий, средний, высокий].
    """
    if features.shape[1] != EXPECTED_N_FEATURES_HONEST:
        raise ValueError(
            f"Ожидалось {EXPECTED_N_FEATURES_HONEST} фич, получено {features.shape[1]}"
        )
    model = get_model()
    return model.predict_proba(features)


def predict_class(features: pd.DataFrame) -> int:
    """Самый вероятный класс (0/1/2)."""
    proba = predict_proba(features)[0]
    return int(np.argmax(proba))


# ============================================================
# HEALTH CHECK
# ============================================================

def check_model_health() -> dict:
    """
    Проверка «модель загружена и предсказывает» для /api/health.

    ⚠️ XGBoost обучен на DataFrame с ИМЕНАМИ колонок —
       predict требует те же имена (иначе feature_names mismatch).
    Поэтому dummy создаётся с колонками из metadata['features'] минус PROXIES.

    Возвращает:
        {
            "status": "ok" | "error",
            "model_type": "XGBClassifier",
            "n_features": 74,
            "classes": [0, 1, 2],
            "error": None | "..."
        }
    """
    try:
        model = get_model()
        meta = get_metadata()

        # Имена колонок — те же, что даёт FeatureBuilder
        # (metadata['features'] минус PROXIES)
        honest_features = [f for f in meta["features"] if f not in PROXIES]
        if len(honest_features) != EXPECTED_N_FEATURES_HONEST:
            raise RuntimeError(
                f"metadata['features'] дал {len(honest_features)} фич, "
                f"ожидалось {EXPECTED_N_FEATURES_HONEST}"
            )

        # Минимальный smoke: 1 строка из 74 нулей с ПРАВИЛЬНЫМИ именами
        dummy = pd.DataFrame(
            np.zeros((1, EXPECTED_N_FEATURES_HONEST)),
            columns=honest_features,
        )
        proba = model.predict_proba(dummy)
        assert proba.shape == (1, 3), f"predict_proba вернул {proba.shape}, ожидалось (1, 3)"

        return {
            "status": "ok",
            "model_type": type(model).__name__,
            "n_features": int(getattr(model, "n_features_in_", EXPECTED_N_FEATURES_HONEST)),
            "classes": [int(c) for c in getattr(model, "classes_", EXPECTED_CLASSES)],
            "error": None,
        }
    except Exception as e:
        logger.error(f"Model health check failed: {e}")
        return {
            "status": "error",
            "model_type": None,
            "n_features": None,
            "classes": None,
            "error": str(e),
        }

def reset_model() -> None:
    """Для тестов: сбросить кэш модели и metadata."""
    global _model, _metadata
    _model = None
    _metadata = None