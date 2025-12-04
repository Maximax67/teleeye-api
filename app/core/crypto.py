import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from app.core.settings import settings


class Crypto:
    def __init__(self, token: str, salt: Optional[str]) -> None:
        self._token = token.encode()
        self._salt = salt.encode() if salt else None

    def get_aes_master_key(self, info: bytes) -> bytes:
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self._salt,
            info=info,
        )

        return hkdf.derive(self._token)

    def encrypt_data(self, data: str, info: bytes) -> bytes:
        key = self.get_aes_master_key(info)
        aes = AESGCM(key)
        nonce = os.urandom(12)
        ct = aes.encrypt(nonce, data.encode(), None)

        return nonce + ct

    def decrypt_data(self, data_bytes: bytes, info: bytes) -> str:
        key = self.get_aes_master_key(info)
        aes = AESGCM(key)
        nonce = data_bytes[:12]
        ct = data_bytes[12:]

        return aes.decrypt(nonce, ct, None).decode()


crypto = Crypto(
    settings.AES_TOKEN.get_secret_value(),
    settings.AES_TOKEN_SALT.get_secret_value() if settings.AES_TOKEN_SALT else None,
)
