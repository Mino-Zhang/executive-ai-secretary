# Official Python 3.12.13 slim-trixie. The reviewed manifest-list digest
# contains both linux/amd64 and linux/arm64 images.
FROM public.ecr.aws/docker/library/python:3.12.13-slim-trixie@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/build/worker/.venv/bin:$PATH"

WORKDIR /build
# uv 0.10.5, pinned to its reviewed multi-platform manifest-list digest.
COPY --from=ghcr.io/astral-sh/uv:0.10.5@sha256:476133fa2aaddb4cbee003e3dc79a88d327a5dc7cb3179b7f02cabd8fdfbcc6e /uv /uvx /bin/
COPY services/api /build/api
COPY services/worker /build/worker
WORKDIR /build/worker
RUN uv sync --frozen --no-dev --no-editable

# Must match the API image and the numeric identity used by `file-tool`.
RUN groupadd --system --gid 999 app \
    && useradd --system --uid 999 --gid app --home-dir /app app
WORKDIR /app
USER app

CMD ["python", "-m", "executive_ai_worker.main"]
