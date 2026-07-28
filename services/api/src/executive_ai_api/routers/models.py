from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..authz import Principal, get_executive_principal
from ..database import get_db
from ..model_authorization import authorized_model_rows, catalog_by_id
from ..schemas import AuthorizedModelOut

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=list[AuthorizedModelOut])
def list_authorized_models(
    principal: Annotated[Principal, Depends(get_executive_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> list[AuthorizedModelOut]:
    catalog = catalog_by_id()
    output: list[AuthorizedModelOut] = []
    for row in authorized_model_rows(db, principal.enterprise_id):
        item = catalog.get(row.model_id)
        if item is None:
            continue
        output.append(
            AuthorizedModelOut(
                model_id=row.model_id,
                name=str(item["name"]),
                family=str(item["family"]),
                profile=str(item["profile"]),
                display_name=(
                    str(item["name"])
                    if not row.display_name or row.display_name == row.model_id
                    else row.display_name
                ),
                is_default=row.is_default,
            )
        )
    return output
