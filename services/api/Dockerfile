# Official Python 3.12.13 slim-trixie. The reviewed manifest-list digest
# contains both linux/amd64 and linux/arm64 images.
FROM public.ecr.aws/docker/library/python:3.12.13-slim-trixie@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Keep the application UID/GID stable across base-image upgrades. The isolated
# backup/restore file tool runs with this numeric identity so it can read and
# restore the private 0700 encrypted-storage tree without DAC override.
RUN groupadd --system --gid 999 app \
    && useradd --system --uid 999 --gid app --home-dir /app app
# uv 0.10.5, pinned to its reviewed multi-platform manifest-list digest.
COPY --from=ghcr.io/astral-sh/uv:0.10.5@sha256:476133fa2aaddb4cbee003e3dc79a88d327a5dc7cb3179b7f02cabd8fdfbcc6e /uv /uvx /bin/
COPY pyproject.toml uv.lock alembic.ini ./
COPY alembic ./alembic
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

RUN mkdir -p /data/files && chown -R app:app /app /data
USER app

EXPOSE 8000
CMD ["uvicorn", "executive_ai_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
