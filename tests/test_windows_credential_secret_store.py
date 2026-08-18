from __future__ import annotations

import ctypes

import pytest


class _FakeFunction:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None
        self.calls: list[tuple] = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.callback(*args)


class _FakeAdvapi32:
    def __init__(self) -> None:
        self.CredWriteW = _FakeFunction(lambda *_: True)
        self.CredReadW = _FakeFunction(lambda *_: False)
        self.CredDeleteW = _FakeFunction(lambda *_: True)
        self.CredFree = _FakeFunction(lambda *_: None)


def _store_with_fake_win_dll(monkeypatch):
    from backend.app.infrastructure import secrets as secret_module

    fake = _FakeAdvapi32()
    monkeypatch.setattr(secret_module.sys, "platform", "win32")
    monkeypatch.setattr(secret_module.ctypes, "WinDLL", lambda *_args, **_kwargs: fake, raising=False)
    return secret_module, secret_module.WindowsCredentialManagerSecretStore(), fake


def test_windows_credential_manager_calls_generic_credential_apis_without_host_access(monkeypatch):
    """Changing Generic Credential marshalling or omitting CredFree must fail this test."""
    secret_module, store, fake = _store_with_fake_win_dll(monkeypatch)

    store.put("config:model:one", "opaque-value")
    written = fake.CredWriteW.calls[0][0]._obj
    assert written.Type == store._CRED_TYPE_GENERIC
    assert written.TargetName == "config:model:one"
    assert written.Persist == store._CRED_PERSIST_LOCAL_MACHINE
    assert written.UserName == "learning-system"
    assert ctypes.string_at(written.CredentialBlob, written.CredentialBlobSize).decode("utf-16-le") == "opaque-value"

    encoded = "read-value".encode("utf-16-le")
    blob = (ctypes.c_byte * len(encoded)).from_buffer_copy(encoded)
    credential = store._credential_type(
        Type=store._CRED_TYPE_GENERIC,
        TargetName="config:model:one",
        CredentialBlobSize=len(encoded),
        CredentialBlob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_byte)),
        Persist=store._CRED_PERSIST_LOCAL_MACHINE,
        UserName="learning-system",
    )
    credential_pointer = ctypes.pointer(credential)

    def read_success(_target, _credential_type, _flags, output_pointer):
        output = ctypes.cast(output_pointer, ctypes.POINTER(ctypes.POINTER(store._credential_type)))
        output[0] = credential_pointer
        return True

    fake.CredReadW.callback = read_success
    assert store.get("config:model:one") == "read-value"
    assert len(fake.CredFree.calls) == 1
    assert ctypes.addressof(fake.CredFree.calls[0][0].contents) == ctypes.addressof(credential)

    store.delete("config:model:one")
    assert fake.CredDeleteW.calls == [("config:model:one", store._CRED_TYPE_GENERIC, 0)]


@pytest.mark.parametrize("operation", ["put", "get", "delete"])
def test_windows_credential_manager_maps_native_api_failures_to_stable_unavailability(monkeypatch, operation):
    """Returning a native API failure must not expose platform-specific details."""
    secret_module, store, fake = _store_with_fake_win_dll(monkeypatch)
    getattr(fake, {"put": "CredWriteW", "get": "CredReadW", "delete": "CredDeleteW"}[operation]).callback = lambda *_: False

    with pytest.raises(secret_module.SecretStoreUnavailable, match=r"^Secret store is unavailable\.$"):
        if operation == "put":
            store.put("config:model:one", "opaque-value")
        elif operation == "get":
            store.get("config:model:one")
        else:
            store.delete("config:model:one")
