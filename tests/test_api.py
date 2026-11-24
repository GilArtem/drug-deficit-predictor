"""
tests/test_api.py
Интеграционные тесты API через FastAPI TestClient.
Проверяет коды ответов (200, 404, 422) и базовую структуру.
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app

# ============================================================
# ФИКСТУРА (Критично для корректного запуска lifespan!)
# ============================================================
@pytest.fixture(scope="module")
def client():
    """
    Гарантирует, что lifespan (startup/shutdown) приложения 
    выполнится корректно до начала тестов и после них.
    """
    with TestClient(app) as c:
        yield c


# ============================================================
# КОНСТАНТЫ ДЛЯ ТЕСТОВ
# ============================================================
VALID_SMNN_CODE = "21.10.51.121-000001-1-00120-0000000000000"  
INVALID_SMNN_CODE = "nonexistent-code-999999999999999999999"


# ============================================================
# ТЕСТЫ
# ============================================================

def test_health_check(client):
    """Health-check должен возвращать 200 (или 503 degraded) и структуру."""
    response = client.get("/api/health")
    assert response.status_code in [200, 503]
    data = response.json()
    assert "status" in data
    assert data["status"] in ["ok", "degraded", "error"]
    assert "components" in data
    assert "database" in data["components"]
    assert "model" in data["components"]


def test_search_success(client):
    """Успешный поиск должен возвращать 200 и структуру SearchResponse."""
    response = client.get("/api/search", params={"q": "ибупрофен", "limit": 5, "offset": 0})
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "items" in data
    assert isinstance(data["items"], list)
    if data["total"] > 0:
        assert "smnn_code" in data["items"][0]
        assert "standard_inn" in data["items"][0]


def test_search_validation_error(client):
    """Пустой запрос 'q' должен возвращать 422 Unprocessable Entity."""
    response = client.get("/api/search", params={"q": "", "limit": 5})
    assert response.status_code == 422


def test_search_limit_validation(client):
    """limit > 100 должен возвращать 422."""
    response = client.get("/api/search", params={"q": "тест", "limit": 150})
    assert response.status_code == 422


def test_risk_not_found(client):
    """Запрос риска для несуществующего СМНН должен возвращать 404."""
    response = client.get(f"/api/risk/{INVALID_SMNN_CODE}")
    assert response.status_code == 404
    data = response.json()
    # FastAPI оборачивает detail в dict при HTTPException
    assert "detail" in data
    assert data["detail"].get("error") == "not_found"


def test_risk_success(client):
    """
    Успешный запрос риска должен возвращать 200 и RiskResponse.
    Если код не найден в БД, тест будет пропущен (skipped), а не упал.
    """
    response = client.get(f"/api/risk/{VALID_SMNN_CODE}")
    
    # Защита: если в тестовой БД этого кода нет, невалим тест, а пропускаем его
    if response.status_code == 404:
        pytest.skip(f"СМНН {VALID_SMNN_CODE} не найден в БД. Замени константу VALID_SMNN_CODE на существующую.")
    
    assert response.status_code == 200
    data = response.json()
    assert "smnn_code" in data
    assert "features_snapshot" in data
    assert "dynamic_data_available" in data
    assert "recommendations" in data
    assert isinstance(data["recommendations"], list)












# """
# tests/test_api.py
# Интеграционные тесты API через FastAPI TestClient.
# Проверяет коды ответов (200, 404, 422) и базовую структуру.
# """
# import pytest
# from fastapi.testclient import TestClient
# from app.main import app

# client = TestClient(app)

# # Известный корректный СМНН для тестов 
# VALID_SMNN_CODE = "21.10.51.121-000001-1-00120-0000000000000"
# INVALID_SMNN_CODE = "nonexistent-code-999999999999999999999"


# def test_health_check():
#     """Health-check должен возвращать 200 и статус 'ok' или 'degraded'."""
#     response = client.get("/api/health")
#     assert response.status_code in [200, 503]
#     data = response.json()
#     assert "status" in data
#     assert data["status"] in ["ok", "degraded", "error"]
#     assert "components" in data
#     assert "database" in data["components"]
#     assert "model" in data["components"]


# def test_search_success():
#     """Успешный поиск должен возвращать 200 и структуру SearchResponse."""
#     response = client.get("/api/search", params={"q": "ибупрофен", "limit": 5, "offset": 0})
#     assert response.status_code == 200
#     data = response.json()
#     assert "total" in data
#     assert "items" in data
#     assert isinstance(data["items"], list)
#     if data["total"] > 0:
#         assert "smnn_code" in data["items"][0]
#         assert "standard_inn" in data["items"][0]


# def test_search_validation_error():
#     """Пустой запрос 'q' должен возвращать 422 Unprocessable Entity."""
#     response = client.get("/api/search", params={"q": "", "limit": 5})
#     assert response.status_code == 422


# def test_search_limit_validation():
#     """limit > 100 должен возвращать 422."""
#     response = client.get("/api/search", params={"q": "тест", "limit": 150})
#     assert response.status_code == 422


# def test_risk_success():
#     """Успешный запрос риска должен возвращать 200 и RiskResponse."""
#     response = client.get(f"/api/risk/{VALID_SMNN_CODE}")
#     if response.status_code == 200:
#         data = response.json()
#         assert "smnn_code" in data
#         assert "features_snapshot" in data
#         assert "dynamic_data_available" in data
#         assert "recommendations" in data
#         assert isinstance(data["recommendations"], list)


# def test_risk_not_found():
#     """Запрос риска для несуществующего СМНН должен возвращать 404."""
#     response = client.get(f"/api/risk/{INVALID_SMNN_CODE}")
#     assert response.status_code == 404
#     data = response.json()
#     assert "detail" in data
#     assert data["detail"]["error"] == "not_found"