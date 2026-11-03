# ============================================================
# Многоступенчатая сборка:
#   Stage 1 (builder): ставим зависимости в отдельную папку
#   Stage 2 (runtime): копируем только нужное
#
# Зачем: финальный image меньше (не тащим компиляторы).
#
# Сборка:  docker build -t drug-deficit-api .
# Запуск:  docker run --env-file .env -v ./models:/app/models drug-deficit-api
# ============================================================


# ============================================================
# Stage 1: builder — устанавливаем зависимости
# ============================================================
FROM python:3.10-slim AS builde

# Не писать .pyc (в контейнере не нужны)
ENV PYTHONDONTWRITEBYTECODE=1
# Не буферизовать stdout/stderr (логи сразу в docker logs)
ENV PYTHONUNBUFFERED=1

WORKDIR /build

# Сначала — только requirements.txt (для кэша слоёв)
COPY requirements.txt .

# Ставим зависимости в /build/venv (изолированно)
RUN python -m venv /build/venv && \
    /build/venv/bin/pip install --upgrade pip && \
    /build/venv/bin/pip install --no-cache-dir -r requirements.txt


# ============================================================
# Stage 2: runtime — финальный образ
# ============================================================
FROM python:3.10-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Системные пакеты: libpq (для psycopg2), curl (для healthcheck)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Копируем venv из builder
COPY --from=builder /build/venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

# Копируем код приложения
COPY app/ ./app/

# Модели и .env НЕ копируем — монтируются volume-ами (см. compose)
# Если хотим запечь модели в образ — раскомментировать:
# COPY models/ ./models/

# Пользователь без root (безопасность)
RUN useradd --create-home --shell /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# Порт
EXPOSE 8000

# Healthcheck (docker сам проверяет — жив ли контейнер)
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Запуск
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]