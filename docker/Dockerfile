# Build stage: install into a virtualenv that the runtime stage copies whole.
FROM python:3.14-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY src/ ./src/
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir ".[server]"

FROM python:3.14-slim
# Non-root: a flaw in the application must not hand over root inside the
# container, which is the first step towards getting out of it.
RUN useradd --system --uid 1000 --create-home spider
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    SPIDER_DATA_DIR=/data
USER spider
EXPOSE 8181
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8181/healthz')"
CMD ["uvicorn", "--factory", "spider.server.app:create_app_from_env", \
     "--host", "0.0.0.0", "--port", "8181"]
