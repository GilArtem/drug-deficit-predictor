"""
app/schemas/response.py — Pydantic-модели для всех ответов API.

Зачем:
- Валидация: FastAPI проверяет, что роутер вернул объект нужной структуры.
- Документация: Swagger UI генерирует описания полей автоматически.
- Типизация: IDE подсказывает поля (response.risk.probability).
- Контракт: фронт видит точную структуру → меньше багов интеграции.

Структура ответов зафиксирована в архитектуре проекта:
- /api/health          → HealthResponse
- /api/search          → SearchResponse
- /api/risk/{code}     → RiskResponse
- /api/top-risks       → TopRisksResponse
- /api/smnn/{code}     → SmnnInfoResponse
- любые ошибки         → ErrorResponse
"""

from __future__ import annotations

from enum import IntEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field



# ============================================================
# КЛАССЫ РИСКА (общий Enum)
# ============================================================

class RiskClass(IntEnum):
    """Класс риска дефицита. Совпадает с target'ом модели (deficit_risk)."""
    LOW = 0       # Низкий
    MEDIUM = 1    # Средний
    HIGH = 2      # Высокий


# ============================================================
# HEALTH
# ============================================================

class ComponentStatus(BaseModel):
    """Статус одного компонента (БД / модель / FeatureBuilder) в /api/health."""
    status: str = Field(..., description="'ok' или 'error'")
    error: Optional[str] = Field(None, description="Текст ошибки, если status='error'")


class DatabaseStatus(ComponentStatus):
    """Статус PostgreSQL — расширяет ComponentStatus."""
    database: Optional[str] = Field(None, description="Имя БД")
    server_version: Optional[str] = Field(None, description="Версия PostgreSQL")
    latency_ms: Optional[float] = Field(None, description="Время ответа, мс")


class ModelStatus(ComponentStatus):
    """Статус ML-модели — расширяет ComponentStatus."""
    model_type: Optional[str] = Field(None, description="Класс модели (XGBClassifier)")
    n_features: Optional[int] = Field(None, description="Число признаков (74)")
    classes: Optional[list[int]] = Field(None, description="Классы модели ([0,1,2])")


class FeatureBuilderStatus(ComponentStatus):
    """Статус FeatureBuilder — расширяет ComponentStatus."""
    n_features: Optional[int] = Field(None, description="Число признаков (74)")
    atc_groups_loaded: Optional[int] = Field(None, description="Сколько ATC-групп в кеше")


class HealthComponents(BaseModel):
    """Все три компонента health-check."""
    database: DatabaseStatus
    model: ModelStatus
    feature_builder: FeatureBuilderStatus


class HealthResponse(BaseModel):
    """Ответ /api/health. HTTP 200 если status='ok', 503 иначе."""
    status: str = Field(..., description="'ok' | 'degraded' | 'error'")
    app: str = Field(..., description="Название приложения")
    version: str = Field(..., description="Версия")
    components: HealthComponents


# ============================================================
# ОШИБКИ
# ============================================================

class ErrorResponse(BaseModel):
    """
    Универсальный ответ на ошибку (404, 500 и т.д.).

    Пример:
        {"error": "not_found", "detail": "СМНН 21.20... не найден", "code": 404}
    """
    error: str = Field(..., description="Машиночитаемый код: not_found, insufficient_data, internal_error")
    detail: str = Field(..., description="Человекочитаемое описание для пользователя")
    code: int = Field(..., description="HTTP-код")


# ============================================================
# РИСК
# ============================================================

class RiskProbabilities(BaseModel):
    """Вероятности трёх классов (сумма = 1.0)."""
    low: float = Field(..., ge=0, le=1, description="Вероятность 'Низкий'")
    medium: float = Field(..., ge=0, le=1, description="Вероятность 'Средний'")
    high: float = Field(..., ge=0, le=1, description="Вероятность 'Высокий'")


class RiskInfo(BaseModel):
    """
    Информация о риске одного СМНН.

    Пример:
        {"class": 2, "label": "Высокий", "probability": 0.87,
         "probabilities": {"low": 0.05, "medium": 0.08, "high": 0.87}}
    """
    risk_class: RiskClass = Field(..., alias="class", description="0/1/2")
    label: str = Field(..., description="'Низкий' | 'Средний' | 'Высокий'")
    probability: float = Field(..., ge=0, le=1, description="Вероятность выбранного класса")
    probabilities: RiskProbabilities

    # ⚠️ populate_by_name=True — разрешает и `risk_class=2`, и `class=2` при создании.
    model_config = ConfigDict(populate_by_name=True)


class FeaturesSnapshot(BaseModel):
    """
    Ключевые признаки для прозрачности прогноза (не все 74, а важные).
    Пользователь видит, на чём основан прогноз.
    """
    products_count: int = Field(..., description="Всего препаратов-позиций у СМНН")
    producers_count: int = Field(..., description="Число производителей")
    is_essential: bool = Field(..., description="ЖНВЛП")
    is_narcotic: bool = Field(..., description="Наркотический/психотропный")
    has_atc_substitutes: bool = Field(..., description="Есть аналоги по ATC")
    avg_age: float = Field(..., description="Средний возраст регистрации, лет")


class RiskResponse(BaseModel):
    """
    Полный ответ /api/risk/{smnn_code}.

    Если нет динамики (dynamic_data_available=False) — risk=None,
    но остальные поля (features, recommendations) остаются.
    """
    smnn_code: str
    standard_inn: str = Field(..., description="МНН")
    standard_form: Optional[str] = Field(None, description="Форма выпуска")
    standard_doze: Optional[str] = Field(None, description="Дозировка")
    trade_names: list[str] = Field(..., description="Торговые названия")

    # None, если dynamic_data_available=False
    risk: Optional[RiskInfo] = Field(None, description="Информация о риске (None, если нет данных)")

    features_snapshot: FeaturesSnapshot
    dynamic_data_available: bool = Field(
        ..., description="Есть ли продажи/остатки для прогноза"
    )
    recommendations: list[str] = Field(
        default_factory=list, description="Рекомендации по действиям"
    )


# ============================================================
# ПОИСК
# ============================================================

class RiskPreview(BaseModel):
    """Краткая превью-информация о риске для карточки поиска."""
    risk_class: RiskClass = Field(..., alias="class")
    label: str
    probability: float = Field(..., ge=0, le=1)

    model_config = ConfigDict(populate_by_name=True)


class SearchResultItem(BaseModel):
    """Одна карточка в /api/search."""
    smnn_code: str
    standard_inn: str
    standard_form: Optional[str] = None
    standard_doze: Optional[str] = None
    trade_names: list[str] = Field(default_factory=list)
    risk_preview: Optional[RiskPreview] = Field(
        None, description="Превью риска (None, если не удалось посчитать)"
    )


class SearchResponse(BaseModel):
    """
    Ответ /api/search?q=...&limit=20&offset=0.

    total — всего найдено (для пагинации), items — текущая страница.
    """
    total: int = Field(..., ge=0, description="Всего найдено СМНН")
    limit: int = Field(..., ge=1, le=100)
    offset: int = Field(..., ge=0)
    items: list[SearchResultItem]


# ============================================================
# ТОП-РИСКИ
# ============================================================

class TopRiskItem(BaseModel):
    """Один элемент /api/top-risks."""
    smnn_code: str
    standard_inn: str
    risk_class: RiskClass = Field(..., alias="class")
    label: str
    probability: float = Field(..., ge=0, le=1)
    is_essential: bool = Field(..., description="ЖНВЛП")

    model_config = ConfigDict(populate_by_name=True)


class TopRisksResponse(BaseModel):
    """
    Ответ /api/top-risks. computed_at — когда пересчитывался кеш.
    """
    computed_at: Optional[str] = Field(
        None, description="Когда пересчитан кеш (ISO 8601) — None, если live"
    )
    items: list[TopRiskItem]


# ============================================================
# ДЕТАЛИ СМНН
# ============================================================

class SmnnInfoResponse(BaseModel):
    """
    Ответ /api/smnn/{smnn_code} — детали СМНН без прогноза.

    Используется для «карточки препарата» без пересчёта модели.
    """
    smnn_code: str
    standard_inn: str
    standard_form: Optional[str] = None
    standard_doze: Optional[str] = None
    trade_names: list[str] = Field(default_factory=list)
    producers: list[str] = Field(default_factory=list, description="Уникальные производители")
    countries: list[str] = Field(default_factory=list, description="Страны-производители")
    atc_code: Optional[str] = Field(None, description="ATC-код (первый валидный)")
    is_essential: bool
    is_narcotic: bool
    products_count: int = Field(..., description="Число товарных позиций (active)")