# Базовый образ зафиксирован по версии и digest (ТЗ п.3.2).
FROM python:3.11.16-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements-model.txt подтягивает requirements.txt + torch/torchvision (CPU)
# для гибридного движка (hybrid.py: CNN для укладки бедра);
# requirements.lock фиксирует версии всех, в т.ч. транзитивных, зависимостей.
COPY requirements.txt requirements-model.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements-model.txt -c requirements.lock

COPY app ./app
COPY ai_module ./ai_module
# Пакетный прогон для сдачи таблицы (ТЗ п.2.5): папка, zip или файл, пример:
#   docker compose run --rm -v /путь/к/данным:/input backend \
#     python batch_predict.py /input/test_set.zip /input/result.xlsx
# То же через REST API: POST /batch (app/routers/batch.py).
COPY batch_predict.py .

RUN mkdir -p /app/data /app/storage

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
