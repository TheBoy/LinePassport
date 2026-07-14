FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Bangkok \
    PORT=8765 \
    OKLINE_STATE_DIR=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl nodejs tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir ".[web,qr]" \
    && useradd --create-home --uid 10001 linepassport \
    && mkdir -p /data \
    && chown -R linepassport:linepassport /data

USER linepassport

EXPOSE 8765
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:${PORT:-8765}/healthz || exit 1

CMD ["sh", "-c", "exec okline web --host 0.0.0.0 --port \"${PORT:-8765}\" --state-dir \"${OKLINE_STATE_DIR:-/data}\" --no-open"]
