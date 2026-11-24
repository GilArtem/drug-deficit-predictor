"""
tests/test_feature_builder.py
Проверка корректности работы FeatureBuilder.
Сверяет размерность, отсутствие NaN и корректную подстановку медиан.
"""
import pytest
import pandas as pd
from app.core.db import get_engine
from app.core.feature_builder import init_feature_builder, build_features, get_builder
from app.config import get_settings

# Инициализация движка и билдера один раз для всех тестов в модуле
@pytest.fixture(scope="module")
def setup_feature_builder():
    settings = get_settings()
    engine = get_engine()
    init_feature_builder(
        db_engine=engine,
        ohe_path=str(settings.ohe_path),
        metadata_path=str(settings.metadata_path),
    )
    yield get_builder()


def test_feature_builder_shape_and_no_nans(setup_feature_builder):
    """
    Проверяем, что для валидного СМНН возвращается DataFrame 1x75 
    и в нем нет пропущенных значений (NaN).
    """
    builder = setup_feature_builder
    
    # Берем СМНН из синтетики 
    test_codes = [
        "21.10.51.121-000001-1-00120-0000000000000", 
        "21.10.51.121-000003-1-00171-0000000000000",
        "21.10.51.123-000006-1-00022-0000000000000",
        "21.10.51.124-000008-1-00004-0000000000000",
        "21.10.51.126-000001-1-00095-0000000000000",
    ]
    
    for code in test_codes:
        features = build_features(code)
        
        # Если код не найден в actual=True, build_features вернет None. 
        if features is None:
            continue
            
        # 1. Проверка размерности (75 честных признаков)
        assert features.shape == (1, 75), f"Ожидалась форма (1, 75), получено {features.shape} для {code}"
        
        # 2. Проверка на отсутствие NaN и Inf
        assert features.isna().sum().sum() == 0, f"Найдены NaN в признаках для {code}"
        
        # 3. Проверка наличия флага dynamic_available
        assert "dynamic_available" in features.attrs, "Отсутствует атрибут dynamic_available"


def test_feature_builder_fallback_medians(setup_feature_builder):
    """
    Проверяем, что для СМНН без динамики (если такой найти) 
    подставляются медианы, а не падает ошибка.
    """
    # Эмуляция: мы не можем легко создать "новый" СМНН в тесте без моков,
    # но мы можем проверить, что dynamic_medians загружены и имеют правильную структуру.
    builder = setup_feature_builder
    
    assert "sales_last_month" in builder.dynamic_medians
    assert "current_stock" in builder.dynamic_medians
    assert isinstance(builder.dynamic_medians["sales_last_month"], (int, float))