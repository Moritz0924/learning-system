class InMemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.events: list[tuple[str, str]] = []

    def put(self, secret_ref: str, value: str) -> None:
        self.events.append(("put", secret_ref))
        self.values[secret_ref] = value

    def get(self, secret_ref: str) -> str:
        return self.values[secret_ref]

    def delete(self, secret_ref: str) -> None:
        self.events.append(("delete", secret_ref))
        self.values.pop(secret_ref, None)
