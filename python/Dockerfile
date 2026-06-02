FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY python/pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY python/loader ./loader

EXPOSE 8001
CMD ["uvicorn", "loader.main:app", "--host", "0.0.0.0", "--port", "8001"]
