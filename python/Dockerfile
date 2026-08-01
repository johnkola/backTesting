FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY python/pyproject.toml ./
# Install the CPU-only PyTorch wheel first so `pip install .` sees torch already
# satisfied and skips the multi-GB CUDA build from PyPI. The NN strategy is a small
# MLP that runs fine on CPU — no GPU stack needed.
RUN pip install --upgrade pip \
    && pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install .

COPY python/loader ./loader
COPY python/nn ./nn

EXPOSE 8001
CMD ["uvicorn", "loader.main:app", "--host", "0.0.0.0", "--port", "8001"]
