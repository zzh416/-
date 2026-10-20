ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-writer-nogui \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown appuser:appuser /app /data

COPY requirements.txt requirements.lock ./
RUN pip install -r requirements.lock

COPY --chown=appuser:appuser . .
COPY docker-entrypoint.sh /usr/local/bin/bagu-entrypoint
RUN chmod 0755 /usr/local/bin/bagu-entrypoint

ENTRYPOINT ["/usr/local/bin/bagu-entrypoint"]

EXPOSE 9000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000"]
