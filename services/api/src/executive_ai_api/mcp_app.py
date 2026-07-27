from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .business_tools import execute_business_tool
from .capabilities import CapabilityClaims, CapabilityError, verify_capability_token
from .config import get_settings
from .database import get_db

settings = get_settings()
app = FastAPI(title="Executive AI controlled business-data hub", docs_url=None)


class ToolRequest(BaseModel):
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)


def capability(authorization: str = Header(default="")) -> CapabilityClaims:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="capability token required")
    try:
        return verify_capability_token(authorization[7:], settings)
    except CapabilityError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/tools/call")
def call_tool(
    request: ToolRequest,
    claims: Annotated[CapabilityClaims, Depends(capability)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    try:
        return execute_business_tool(db, claims, request.tool, request.arguments)
    except CapabilityError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
