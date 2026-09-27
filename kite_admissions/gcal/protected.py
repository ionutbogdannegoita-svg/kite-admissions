"""Protezione delle credenziali Google nel profilo Windows (SPEC §4.1, §10).

Usa DPAPI (CryptProtectData) legata all'utente Windows corrente: il file cifrato non è
leggibile da un altro utente né su un altro PC. Su un nuovo PC si ricollega Google.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_ENTROPY = b"KITE Admissions / credenziali Google / v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class ProtectionError(Exception):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


class WindowsProtectedStore:
    """Cifratura DPAPI dell'utente corrente."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise ProtectionError("La protezione DPAPI è disponibile soltanto su Windows.")
        self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        blob_ptr = ctypes.POINTER(_Blob)
        for name in ("CryptProtectData", "CryptUnprotectData"):
            function = getattr(self._crypt32, name)
            function.argtypes = [blob_ptr, ctypes.c_void_p, blob_ptr, ctypes.c_void_p, ctypes.c_void_p,
                                 wintypes.DWORD, blob_ptr]
            function.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    def _call(self, function, data: bytes) -> bytes:
        data_in, _keep_data = _blob(data)
        entropy, _keep_entropy = _blob(_ENTROPY)
        data_out = _Blob()
        ok = function(ctypes.byref(data_in), None, ctypes.byref(entropy), None, None,
                      _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(data_out))
        if not ok:
            raise ProtectionError(f"Operazione DPAPI non riuscita (errore {ctypes.get_last_error()}).")
        try:
            return ctypes.string_at(data_out.pbData, data_out.cbData)
        finally:
            self._kernel32.LocalFree(data_out.pbData)

    def protect(self, data: bytes) -> bytes:
        return self._call(self._crypt32.CryptProtectData, data)

    def unprotect(self, blob: bytes) -> bytes:
        return self._call(self._crypt32.CryptUnprotectData, blob)
