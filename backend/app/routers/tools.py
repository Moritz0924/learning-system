from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_principal
from backend.app.core.principal import Principal
from backend.app.db import get_session
from backend.app.services.learning_sources import LearningSourceSearchUnavailable, search_learning_sources
from backend.app.services.official_sources import OfficialSourceSearchUnavailable, search_official_learning_sources


router = APIRouter(prefix="/api/tools", tags=["tools"])


class OfficialSourceSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    domains: list[str] = Field(default_factory=list)


class LearningSourceSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=512)


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


@router.post("/search-learning-sources")
def search_learning_sources_endpoint(
    payload: LearningSourceSearchRequest,
    _principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> dict:
    invalid_detail = None
    unavailable = False
    try:
        results = search_learning_sources(session, query=payload.query)
    except ValueError as exc:
        invalid_detail = str(exc)
    except LearningSourceSearchUnavailable:
        unavailable = True
    if invalid_detail is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=invalid_detail)
    if unavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "source_search.unavailable",
                "message": "Online learning source search is unavailable.",
            },
        )
    return {"results": results}
