from backend.app.main import app
from backend.app.routers.config import get_secret_store


class _E2ESecretStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def put(self, secret_ref: str, value: str) -> None:
        self._values[secret_ref] = value

    def get(self, secret_ref: str) -> str:
        return self._values[secret_ref]

    def delete(self, secret_ref: str) -> None:
        self._values.pop(secret_ref, None)


_secret_store = _E2ESecretStore()
app.dependency_overrides[get_secret_store] = lambda: _secret_store
