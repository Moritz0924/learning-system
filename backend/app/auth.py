from fastapi import Header, HTTPException, status


def get_current_user_id(x_user_id: str | None = Header(default=None, alias="X-User-Id")) -> str:
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header is required for this stage 1 auth placeholder.",
        )
    return x_user_id


def validate_legacy_user_id(legacy_user_id: str | None, current_user_id: str) -> None:
    if legacy_user_id is not None and legacy_user_id != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="legacy user_id must match X-User-Id header",
        )
