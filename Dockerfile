FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    EXPENSES_DB=/app/data/expenses.db

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Non-root user; data dir owned by it so the mounted volume is writable.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown appuser:appuser /app/data

COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4)"

# 0.0.0.0 is inside the container only; docker-compose publishes on 127.0.0.1.
CMD ["streamlit", "run", "app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
