FROM python:3.13-slim AS dependencies
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.lock.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

FROM dependencies AS test
COPY tests ./tests
RUN python -m pytest -q

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"
RUN groupadd --gid 10001 abeba && useradd --uid 10001 --gid abeba --create-home abeba
WORKDIR /app
COPY --from=dependencies /opt/venv /opt/venv
USER 10001:10001
ENV ABEBA_BOT_HEALTH_HOST=0.0.0.0 ABEBA_BOT_HEALTH_PORT=8081
EXPOSE 8081
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('ABEBA_BOT_HEALTH_PORT', '8081') + '/healthz', timeout=3).read()" || exit 1
CMD ["python", "-m", "abeba_bot"]
