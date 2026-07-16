from fastapi import Depends, HTTPException, status

from backend.app.api.deps import get_current_principal_compat
from backend.app.core.principal import Principal


def get_current_user_id(principal: Principal = Depends(get_current_principal_compat)) -> str:
    """Compatibility shim for old routers; identity always originates in Principal."""
    return principal.user_id


def validate_legacy_user_id(legacy_user_id: str | None, current_user_id: str) -> None:
    if legacy_user_id is not None and legacy_user_id != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="legacy user_id must match X-User-Id header",
        )
