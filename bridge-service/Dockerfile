FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

RUN groupadd --gid 10001 bridge \
    && useradd --uid 10001 --gid bridge --create-home bridge \
    && mkdir /data \
    && chown bridge:bridge /data

USER bridge
CMD ["uvicorn", "hermes_bridge.app:create_app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8787"]
