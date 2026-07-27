FROM public.ecr.aws/docker/library/python:3.12.13-slim-trixie@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    HOME=/tmp/hermes-home

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.10.5@sha256:476133fa2aaddb4cbee003e3dc79a88d327a5dc7cb3179b7f02cabd8fdfbcc6e /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable
RUN groupadd --system --gid 1001 app && useradd --system --uid 1001 --gid app app
USER app
EXPOSE 8020
CMD ["uvicorn", "executive_ai_hermes.main:app", "--host", "0.0.0.0", "--port", "8020"]
