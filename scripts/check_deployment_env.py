"""Validate a Compose env file without printing secrets."""
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

from app.core.auth import configured_principals


def main(path: Path) -> int:
    values = dotenv_values(path)
    checks = {}
    required = ("POSTGRES_PASSWORD", "DATABASE_URL", "ENCRYPTION_SECRET_KEY",
                "API_TOKENS_JSON", "LLM_PROVIDER")
    checks["required values"] = all(values.get(name) for name in required)
    try:
        Fernet(values["ENCRYPTION_SECRET_KEY"].encode("ascii"))
        checks["Fernet key"] = True
    except (ValueError, TypeError, KeyError, UnicodeEncodeError):
        checks["Fernet key"] = False
    try:
        checks["database password match"] = (
            make_url(values["DATABASE_URL"]).password == values.get("POSTGRES_PASSWORD"))
    except (ValueError, TypeError, KeyError):
        checks["database password match"] = False
    try:
        checks["API credentials"] = bool(configured_principals(values.get("API_TOKENS_JSON")))
    except ValueError:
        checks["API credentials"] = False
    provider = (values.get("LLM_PROVIDER") or "").lower()
    provider_field = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY",
                      "openrouter": "OPENROUTER_API_KEY"}.get(provider)
    checks["live provider credential"] = bool(provider_field and values.get(provider_field))
    for name, passed in checks.items():
        print(f"{name}: {'OK' if passed else 'FAIL'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "docker/.env")))
