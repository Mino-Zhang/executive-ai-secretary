from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import ORJSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .audit_integrity import initialize_audit_chains
from .authz import csrf_protect
from .config import get_settings
from .database import engine
from .errors import (
    AppError,
    app_error_handler,
    http_error_handler,
    validation_error_handler,
)
from .logging_config import configure_logging
from .middleware import RequestContextMiddleware
from .routers import (
    admin,
    auth,
    conversations,
    files,
    health,
    jobs,
    memories,
    organizations,
    projects,
    reports,
)

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Settings construction is deliberately part of startup; unsafe production values fail closed.
    settings.decoded_file_encryption_key()
    with engine.begin() as connection:
        initialize_audit_chains(connection)
    yield


app = FastAPI(
    title="董事长人工智能研究员 API",
    description=(
        "企业经营决策工作台正式 API。所有业务接口均使用服务端 Session、CSRF 与数据范围校验。"
    ),
    version=settings.app_version,
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
    docs_url="/api/docs" if settings.app_mode != "production" else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.app_mode != "production" else None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID", "Idempotency-Key"],
    expose_headers=["X-Request-ID", "Retry-After"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
app.add_middleware(RequestContextMiddleware)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(HTTPException, http_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)

app.include_router(health.router)
protected_dependencies = [Depends(csrf_protect)]
for api_router in (
    auth.router,
    organizations.router,
    conversations.router,
    projects.router,
    files.router,
    memories.router,
    reports.router,
    jobs.router,
    admin.router,
):
    app.include_router(
        api_router,
        prefix=settings.api_prefix,
        dependencies=protected_dependencies,
    )


def custom_openapi() -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    components = schema.setdefault("components", {})
    schemes = components.setdefault("securitySchemes", {})
    schemes["sessionCookie"] = {
        "type": "apiKey",
        "in": "cookie",
        "name": settings.session_cookie_name,
        "description": "HttpOnly opaque session token. Unsafe requests also require X-CSRF-Token.",
    }
    for path, methods in schema.get("paths", {}).items():
        if path.startswith(settings.api_prefix) and path != f"{settings.api_prefix}/auth/login":
            for operation in methods.values():
                if isinstance(operation, dict):
                    operation.setdefault("security", [{"sessionCookie": []}])
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi
