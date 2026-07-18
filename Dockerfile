FROM python:3.12.11-slim-bookworm AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY requirements.lock pyproject.toml ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip==25.1.1 \
 && /opt/venv/bin/pip install -r requirements.lock
COPY src ./src
RUN /opt/venv/bin/pip install --no-deps .

FROM python:3.12.11-slim-bookworm AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN groupadd --gid 10001 recorder \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent recorder
COPY --from=builder /opt/venv /opt/venv
COPY --chown=recorder:recorder config /app/config
COPY --chown=recorder:recorder fixtures /app/fixtures
COPY --chown=recorder:recorder scripts/container-entrypoint /usr/local/bin/container-entrypoint
WORKDIR /app
USER 10001:10001
ENTRYPOINT ["/usr/local/bin/container-entrypoint"]
CMD ["run"]
