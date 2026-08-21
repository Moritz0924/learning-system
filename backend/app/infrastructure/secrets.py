from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Protocol


class SecretStore(Protocol):
    def put(self, secret_ref: str, value: str) -> None: ...

    def get(self, secret_ref: str) -> str: ...

    def delete(self, secret_ref: str) -> None: ...


class SecretStoreUnavailable(RuntimeError):
    """Raised when the operating-system secret store cannot be used."""

    def __init__(self) -> None:
        super().__init__("Secret store is unavailable.")


class WindowsCredentialManagerSecretStore:
    """Credential Manager-backed store for user configuration secrets."""

    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise SecretStoreUnavailable()
        try:
            self._advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        except (AttributeError, OSError) as exc:
            raise SecretStoreUnavailable() from exc
        self._credential_type = self._make_credential_type()
        self._advapi32.CredWriteW.argtypes = [ctypes.POINTER(self._credential_type), wintypes.DWORD]
        self._advapi32.CredWriteW.restype = wintypes.BOOL
        self._advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(self._credential_type))]
        self._advapi32.CredReadW.restype = wintypes.BOOL
        self._advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._advapi32.CredDeleteW.restype = wintypes.BOOL
        self._advapi32.CredFree.argtypes = [ctypes.c_void_p]
        self._advapi32.CredFree.restype = None

    @staticmethod
    def _make_credential_type():
        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        return CREDENTIALW

    def put(self, secret_ref: str, value: str) -> None:
        encoded = value.encode("utf-16-le")
        blob = (ctypes.c_byte * len(encoded)).from_buffer_copy(encoded)
        credential = self._credential_type(
            Type=self._CRED_TYPE_GENERIC,
            TargetName=secret_ref,
            CredentialBlobSize=len(encoded),
            CredentialBlob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_byte)),
            Persist=self._CRED_PERSIST_LOCAL_MACHINE,
            UserName="learning-system",
        )
        if not self._advapi32.CredWriteW(ctypes.byref(credential), 0):
            raise SecretStoreUnavailable()

    def get(self, secret_ref: str) -> str:
        credential_pointer = ctypes.POINTER(self._credential_type)()
        if not self._advapi32.CredReadW(secret_ref, self._CRED_TYPE_GENERIC, 0, ctypes.byref(credential_pointer)):
            raise SecretStoreUnavailable()
        try:
            credential = credential_pointer.contents
            if credential.CredentialBlobSize % 2:
                raise SecretStoreUnavailable()
            return ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize).decode("utf-16-le")
        finally:
            self._advapi32.CredFree(credential_pointer)

    def delete(self, secret_ref: str) -> None:
        if not self._advapi32.CredDeleteW(secret_ref, self._CRED_TYPE_GENERIC, 0):
            raise SecretStoreUnavailable()
