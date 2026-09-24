"""Single-organization bearer credentials. Multi-tenant mode is unsupported."""
import hmac
import json
from dataclasses import dataclass

from fastapi import HTTPException, Request

from app.config import settings


@dataclass(frozen=True)
class Principal:
    id: str
    role: str


def configured_principals(raw):
    if not raw:
        return []
    try:
        entries = json.loads(raw)
        if not isinstance(entries, list) or not entries:
            raise ValueError("API_TOKENS_JSON must be a nonempty list")
        ids, tokens = set(), set()
        for entry in entries:
            if (not isinstance(entry, dict) or set(entry) != {"id", "role", "token"}
                    or not isinstance(entry["id"], str) or not entry["id"]
                    or len(entry["id"]) > 64 or entry["id"] == "local"
                    or entry["role"] not in {"admin", "recruiter"}
                    or not isinstance(entry["token"], str) or len(entry["token"]) < 32
                    or entry["id"] in ids or entry["token"] in tokens):
                raise ValueError("Invalid or duplicate API credential")
            ids.add(entry["id"])
            tokens.add(entry["token"])
        return entries
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid API_TOKENS_JSON") from exc


def current_principal(request: Request) -> Principal:
    if settings.ENVIRONMENT.lower() != "production":
        return Principal("local", "admin")
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Authentication required",
                            headers={"WWW-Authenticate": "Bearer"})
    entries = configured_principals(settings.API_TOKENS_JSON)
    match = next((entry for entry in entries if hmac.compare_digest(token, entry["token"])), None)
    if match is None:
        raise HTTPException(status_code=401, detail="Invalid credential",
                            headers={"WWW-Authenticate": "Bearer"})
    return Principal(match["id"], match["role"])


def can_access(owner_id: str, principal: Principal) -> bool:
    return principal.role == "admin" or owner_id == principal.id
