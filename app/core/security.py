from cryptography.fernet import Fernet
from app.config import settings


def _get_fernet_cipher() -> Fernet:
    key = settings.ENCRYPTION_SECRET_KEY
    if not key:
        raise ValueError("ENCRYPTION_SECRET_KEY must be configured with a Fernet key")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("ENCRYPTION_SECRET_KEY must be a valid Fernet key") from exc


def validate_encryption_configuration() -> None:
    _get_fernet_cipher()


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
