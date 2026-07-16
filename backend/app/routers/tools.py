from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_principal
from backend.app.core.principal import Principal
from backend.app.db import get_session
from backend.app.services.official_sources import OfficialSourceSearchUnavailable, search_official_learning_sources


router = APIRouter(prefix="/api/tools", tags=["tools"])


class OfficialSourceSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    domains: list[str] = Field(default_factory=list)


@router.post("/search-official-learning-sources")
def search_official_sources_endpoint(
    payload: OfficialSourceSearchRequest,
    _principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> dict:
    try:
        return {
            "results": search_official_learning_sources(
                session,
                query=payload.query,
                domains=payload.domains,
            )
        }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OfficialSourceSearchUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
