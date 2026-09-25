"""Authentication adapters produce one stable principal for authorization.

Password login issues short-lived JWTs. An OIDC adapter can later resolve its
issuer/subject to a recruiter_users.id and return the same Principal.
"""
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.database import RecruiterUserModel, get_db

ACCESS_COOKIE = "cv_access"
CSRF_COOKIE = "cv_csrf"
ISSUER = "cv-screening"
AUDIENCE = "cv-screening-api"
_dev_secret = secrets.token_urlsafe(48)
_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)


@dataclass(frozen=True)
class Principal:
    id: str
    role: str


def signing_key() -> str:
    if settings.JWT_SECRET_KEY:
        return settings.JWT_SECRET_KEY
    if settings.ENVIRONMENT.lower() == "production":
        raise RuntimeError("JWT_SECRET_KEY is required")
    return _dev_secret


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 1024:
        raise ValueError("Password must contain 12 to 1024 characters")
    return _hasher.hash(password)


def verify_password(stored: str, password: str) -> bool:
    try:
        return _hasher.verify(stored, password)
    except (InvalidHashError, VerifyMismatchError):
        return False


def issue_access_token(user: RecruiterUserModel) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    csrf = secrets.token_urlsafe(32)
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": user.id, "iat": now,
              "nbf": now, "exp": now + timedelta(minutes=settings.JWT_ACCESS_MINUTES),
              "jti": secrets.token_urlsafe(16), "ver": user.token_version,
              "csrf": hashlib.sha256(csrf.encode()).hexdigest()}
    return jwt.encode(claims, signing_key(), algorithm="HS256"), csrf


def configured_principals(raw):
    """Legacy service credentials; never sent to the browser."""
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


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="Authentication required",
                         headers={"WWW-Authenticate": "Bearer"})


async def current_principal(request: Request, db: AsyncSession = Depends(get_db)) -> Principal:
    header = request.headers.get("authorization", "")
    scheme, _, bearer = header.partition(" ")
    cookie_token = request.cookies.get(ACCESS_COOKIE)
    if scheme.lower() == "bearer" and bearer:
        match = next((entry for entry in configured_principals(settings.API_TOKENS_JSON)
                      if hmac.compare_digest(bearer, entry["token"])), None)
        if match:
            return Principal(match["id"], match["role"])
        token = bearer
    elif cookie_token:
        token = cookie_token
    elif settings.ENVIRONMENT.lower() != "production":
        return Principal("local", "admin")
    else:
        raise _unauthorized()
    try:
        claims = jwt.decode(token, signing_key(), algorithms=["HS256"],
                            issuer=ISSUER, audience=AUDIENCE,
                            options={"require": ["iss", "aud", "sub", "iat", "nbf", "exp", "jti", "ver", "csrf"]})
        if not isinstance(claims["sub"], str) or not isinstance(claims["ver"], int):
            raise ValueError("Invalid claims")
    except (jwt.PyJWTError, ValueError) as exc:
        raise _unauthorized() from exc
    user = await db.get(RecruiterUserModel, claims["sub"])
    if user is None or not user.is_active or user.token_version != claims["ver"]:
        raise _unauthorized()
    if token == cookie_token and request.method not in {"GET", "HEAD", "OPTIONS"}:
        cookie_csrf = request.cookies.get(CSRF_COOKIE, "")
        header_csrf = request.headers.get("x-csrf-token", "")
        if (not cookie_csrf or not hmac.compare_digest(cookie_csrf, header_csrf)
                or not hmac.compare_digest(hashlib.sha256(cookie_csrf.encode()).hexdigest(), claims["csrf"])):
            raise HTTPException(status_code=403, detail="CSRF check failed")
    return Principal(user.id, user.role)


def can_access(owner_id: str, principal: Principal) -> bool:
    return principal.role == "admin" or owner_id == principal.id
