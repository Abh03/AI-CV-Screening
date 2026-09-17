import base64
import hashlib
from cryptography.fernet import Fernet
from app.config import settings


def _get_fernet_cipher() -> Fernet:
    secret = getattr(settings, "ENCRYPTION_SECRET_KEY", "fallback_dev_secret_key_32_bytes_len=")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt_payload(data: str | bytes) -> bytes:
    """Encrypts raw text payload for resting PostgreSQL BYTEA storage."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    cipher = _get_fernet_cipher()
    return cipher.encrypt(data)


def decrypt_payload(token: bytes) -> str:
    """Decrypts ciphertext from PostgreSQL BYTEA storage back into UTF-8 text."""
    cipher = _get_fernet_cipher()
    decrypted_bytes = cipher.decrypt(token)
    return decrypted_bytes.decode("utf-8")