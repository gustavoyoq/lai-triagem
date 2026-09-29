FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY artifacts/registered ./artifacts/registered
COPY configs ./configs
RUN groupadd --system app && useradd --system --gid app --no-create-home app \
    && mkdir -p /app/monitoring/telemetry \
    && chown -R app:app /app

USER app
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:3000/healthz', timeout=3)"
CMD ["bentoml", "serve", "mlops_lai.service:LaiTriagem", "--port", "3000"]
