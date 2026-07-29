from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Any | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


def error_payload(request: Request, code: str, message: str, details: Any | None = None) -> dict:
    payload: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(request.state, "request_id", None),
        }
    }
    if details is not None:
        payload["error"]["details"] = details
    return payload


async def app_error_handler(request: Request, exc: AppError) -> ORJSONResponse:
    return ORJSONResponse(
        status_code=exc.status_code,
        content=error_payload(request, exc.code, exc.message, exc.details),
    )


async def http_error_handler(request: Request, exc: HTTPException) -> ORJSONResponse:
    if isinstance(exc.detail, dict):
        code = str(exc.detail.get("code", "http_error"))
        message = str(exc.detail.get("message", "请求无法完成"))
        details = exc.detail.get("details")
    else:
        code = "http_error"
        message = str(exc.detail)
        details = None
    return ORJSONResponse(
        status_code=exc.status_code,
        content=error_payload(request, code, message, details),
        headers=exc.headers,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> ORJSONResponse:
    safe_errors = []
    for item in exc.errors():
        safe_errors.append(
            {
                "location": [str(value) for value in item.get("loc", ())],
                "message": item.get("msg", "invalid value"),
                "type": item.get("type", "validation_error"),
            }
        )
    return ORJSONResponse(
        status_code=422,
        content=error_payload(request, "validation_error", "请求参数不符合要求", safe_errors),
    )
