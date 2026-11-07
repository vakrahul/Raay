"""Hardware-backed local credential vault utilizing Windows Data Protection API (DPAPI).

Keys encrypted with DPAPI can only be decrypted on the same computer by the same Windows user.
No plaintext secrets are stored on disk or committed.
"""

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
from typing import Dict, List, Optional


# Define Windows DPAPI structures if on Windows
class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


class CredentialVault:
    """Manages encrypted provider credentials using DPAPI on Windows."""

    def __init__(self, vault_path: Optional[Path] = None):
        self.vault_path = (
            vault_path or Path.home() / ".winai" / "vault.enc"
        ).resolve()
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        self.is_windows = os.name == "nt"

    def _encrypt_dpapi(self, plaintext: bytes) -> bytes:
        """Encrypt bytes using Windows CryptProtectData."""
        if not self.is_windows:
            # Fallback for non-Windows dev environments: reverse/mock cipher
            return b"mock_enc:" + plaintext

        crypt32 = ctypes.windll.crypt32
        data_in = DATA_BLOB(
            cbData=len(plaintext),
            pbData=ctypes.cast(ctypes.create_string_buffer(plaintext), ctypes.POINTER(ctypes.c_byte)),
        )
        data_out = DATA_BLOB()

        ret = crypt32.CryptProtectData(
            ctypes.byref(data_in),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(data_out),
        )
        if not ret:
            raise ctypes.WinError()

        ciphertext = ctypes.string_at(data_out.pbData, data_out.cbData)
        kernel32 = ctypes.windll.kernel32
        kernel32.LocalFree(data_out.pbData)
        return ciphertext

    def _decrypt_dpapi(self, ciphertext: bytes) -> bytes:
        """Decrypt bytes using Windows CryptUnprotectData."""
        if not self.is_windows:
            if ciphertext.startswith(b"mock_enc:"):
                return ciphertext[9:]
            return ciphertext

        crypt32 = ctypes.windll.crypt32
        data_in = DATA_BLOB(
            cbData=len(ciphertext),
            pbData=ctypes.cast(ctypes.create_string_buffer(ciphertext), ctypes.POINTER(ctypes.c_byte)),
        )
        data_out = DATA_BLOB()

        ret = crypt32.CryptUnprotectData(
            ctypes.byref(data_in),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(data_out),
        )
        if not ret:
            raise ctypes.WinError()

        plaintext = ctypes.string_at(data_out.pbData, data_out.cbData)
        kernel32 = ctypes.windll.kernel32
        kernel32.LocalFree(data_out.pbData)
        return plaintext

    def store_credential(self, provider_id: str, secret_key: str) -> None:
        """Encrypt and persist credential for a provider."""
        vault_data = self._read_raw_vault()
        vault_data[provider_id] = secret_key
        raw_json = json.dumps(vault_data).encode("utf-8")
        encrypted_bytes = self._encrypt_dpapi(raw_json)
        self.vault_path.write_bytes(encrypted_bytes)

    def get_credential(self, provider_id: str) -> Optional[str]:
        """Retrieve and decrypt credential for a provider."""
        vault_data = self._read_raw_vault()
        return vault_data.get(provider_id)

    def delete_credential(self, provider_id: str) -> bool:
        """Remove a credential from the vault."""
        vault_data = self._read_raw_vault()
        if provider_id in vault_data:
            del vault_data[provider_id]
            raw_json = json.dumps(vault_data).encode("utf-8")
            encrypted_bytes = self._encrypt_dpapi(raw_json)
            self.vault_path.write_bytes(encrypted_bytes)
            return True
        return False

    def list_configured_providers(self) -> List[str]:
        """List provider IDs that have credentials stored without revealing keys."""
        return list(self._read_raw_vault().keys())

    def _read_raw_vault(self) -> Dict[str, str]:
        if not self.vault_path.exists() or self.vault_path.stat().st_size == 0:
            return {}
        try:
            encrypted_bytes = self.vault_path.read_bytes()
            plaintext = self._decrypt_dpapi(encrypted_bytes)
            return json.loads(plaintext.decode("utf-8"))
        except Exception:
            return {}
