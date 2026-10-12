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

# Uses https when a certificate is configured (public mode); the self-signed cert is not verified here.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, ssl, urllib.request; s = 'https' if os.environ.get('STREAMLIT_SERVER_SSL_CERT_FILE') else 'http'; urllib.request.urlopen(f'{s}://127.0.0.1:8501/_stcore/health', timeout=4, context=ssl._create_unverified_context())"

# 0.0.0.0 is inside the container only; docker-compose publishes on 127.0.0.1.
CMD ["streamlit", "run", "app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
