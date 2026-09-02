from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_principal
from backend.app.application.saved_learning_node_service import (
    delete_saved_learning_node,
    list_saved_learning_nodes,
    save_learning_node,
)
from backend.app.core.principal import Principal
from backend.app.db import get_session


router = APIRouter(prefix="/api/saved-learning-nodes", tags=["saved-learning-nodes"])


class SavedLearningNodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal_id: str


class SavedLearningNodeListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    knowledge_node_ids: list[str]


@router.get("", response_model=SavedLearningNodeListResponse)
def list_saved_learning_nodes_endpoint(
    goal_id: str = Query(min_length=1),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> dict:
    try:
        return {
            "knowledge_node_ids": list_saved_learning_nodes(
                session,
                user_id=principal.user_id,
                goal_id=goal_id,
            )
        }
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/{knowledge_node_id}", status_code=status.HTTP_204_NO_CONTENT)
def save_learning_node_endpoint(
    knowledge_node_id: str,
    payload: SavedLearningNodeRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> Response:
    try:
        save_learning_node(
            session,
            user_id=principal.user_id,
            goal_id=payload.goal_id,
            knowledge_node_id=knowledge_node_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{knowledge_node_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved_learning_node_endpoint(
    knowledge_node_id: str,
    goal_id: str = Query(min_length=1),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_session),
) -> Response:
    try:
        delete_saved_learning_node(
            session,
            user_id=principal.user_id,
            goal_id=goal_id,
            knowledge_node_id=knowledge_node_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
