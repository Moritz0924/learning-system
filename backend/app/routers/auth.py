from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_principal
from backend.app.api.schemas.auth import AuthTokenResponse, AuthUserResponse, LoginRequest, RegisterRequest
from backend.app.application.auth_service import AuthError, AuthService, InvalidCredentials, InvalidRefresh, RefreshRace
from backend.app.core.principal import Principal
from backend.app.core.security import auth_settings
from backend.app.db import get_session
from backend.app.infrastructure.auth.cookies import clear_refresh_cookie, set_refresh_cookie
from backend.app.infrastructure.auth.origin_validator import require_allowed_origin


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=AuthTokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, response: Response, session: Session = Depends(get_session)) -> AuthTokenResponse:
    settings = auth_settings()
    require_allowed_origin(request, settings)
    try:
        result = AuthService(session, settings).register(email=str(payload.email), password=payload.password, display_name=payload.display_name, user_agent=request.headers.get("user-agent"))
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "auth.email_already_registered"}) from exc
    set_refresh_cookie(response, result.refresh_cookie_value, settings)
    return _response(result)


@router.post("/login", response_model=AuthTokenResponse)
def login(payload: LoginRequest, request: Request, response: Response, session: Session = Depends(get_session)) -> AuthTokenResponse:
    settings = auth_settings()
    require_allowed_origin(request, settings)
    try:
        result = AuthService(session, settings).login(email=str(payload.email), password=payload.password, user_agent=request.headers.get("user-agent"))
    except InvalidCredentials as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "auth.invalid_credentials", "message": "Email or password is incorrect."}) from exc
    set_refresh_cookie(response, result.refresh_cookie_value, settings)
    return _response(result)


@router.post("/refresh", response_model=AuthTokenResponse)
def refresh(request: Request, response: Response, session: Session = Depends(get_session)) -> AuthTokenResponse:
    settings = auth_settings()
    require_allowed_origin(request, settings)
    try:
        result = AuthService(session, settings).refresh(cookie_value=request.cookies.get(settings.cookie_name), user_agent=request.headers.get("user-agent"))
    except RefreshRace as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": "auth.refresh_race", "message": "A concurrent token refresh is already in progress."}) from exc
    except InvalidRefresh as exc:
        clear_refresh_cookie(response, settings)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "auth.invalid_refresh_token"}) from exc
    set_refresh_cookie(response, result.refresh_cookie_value, settings)
    return _response(result)


@router.get("/me", response_model=AuthUserResponse)
def me(principal: Principal = Depends(get_current_principal), session: Session = Depends(get_session)) -> AuthUserResponse:
    user = AuthService(session, auth_settings())._repository.get_active_user(principal.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return _user(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, principal: Principal = Depends(get_current_principal), session: Session = Depends(get_session)) -> Response:
    settings = auth_settings()
    require_allowed_origin(request, settings)
    AuthService(session, settings).logout(session_id=principal.session_id)
    clear_refresh_cookie(response, settings)
    return response


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(request: Request, response: Response, principal: Principal = Depends(get_current_principal), session: Session = Depends(get_session)) -> Response:
    settings = auth_settings()
    require_allowed_origin(request, settings)
    AuthService(session, settings).logout_all(user_id=principal.user_id)
    clear_refresh_cookie(response, settings)
    return response


def _response(result) -> AuthTokenResponse:
    return AuthTokenResponse(access_token=result.access_token, expires_in=result.expires_in, user=_user(result.user))


def _user(user) -> AuthUserResponse:
    return AuthUserResponse(id=user.id, email=user.email, display_name=user.display_name, role=user.role, status=user.status)
