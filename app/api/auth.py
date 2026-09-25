"""First-party recruiter login; browser tokens stay in HttpOnly cookies."""
import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import (ACCESS_COOKIE, CSRF_COOKIE, Principal, current_principal,
                           hash_password, issue_access_token, verify_password)
from app.models.database import RecruiterUserModel, get_db

router = APIRouter(prefix="/api/v1/auth", tags=["Recruiter authentication"])
_dummy_hash = hash_password("invalid-password-placeholder")


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


def _same_origin(request: Request) -> None:
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="Cross-site request denied")
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != request.headers.get("host", "").lower():
            raise HTTPException(status_code=403, detail="Cross-site request denied")


def _public_user(user: RecruiterUserModel) -> dict:
    return {"id": user.id, "email": user.email, "username": user.username, "role": user.role}


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response,
                db: AsyncSession = Depends(get_db)):
    _same_origin(request)
    identifier = payload.identifier.strip().lower()
    user = (await db.execute(select(RecruiterUserModel).where(
        (func.lower(RecruiterUserModel.email) == identifier) |
        (func.lower(RecruiterUserModel.username) == identifier)).with_for_update())).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    valid = await asyncio.to_thread(verify_password, user.password_hash if user else _dummy_hash, payload.password)
    locked = user is not None and user.locked_until is not None and user.locked_until.replace(tzinfo=timezone.utc) > now
    if user is None or not user.is_active or locked or not valid:
        if user is not None and not locked:
            user.failed_attempts += 1
            if user.failed_attempts >= 5:
                user.locked_until = now + timedelta(minutes=15)
                user.failed_attempts = 0
            await db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")
    user.failed_attempts = 0
    user.locked_until = None
    await db.commit()
    token, csrf = issue_access_token(user)
    secure = settings.ENVIRONMENT.lower() == "production"
    max_age = settings.JWT_ACCESS_MINUTES * 60
    response.set_cookie(ACCESS_COOKIE, token, max_age=max_age, httponly=True,
                        secure=secure, samesite="lax", path="/")
    response.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, httponly=False,
                        secure=secure, samesite="lax", path="/")
    return {"user": _public_user(user), "expires_in": max_age}


@router.get("/me")
async def me(principal: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)):
    if principal.id == "local" and settings.ENVIRONMENT.lower() != "production":
        return {"user": {"id": "local", "email": "", "username": "Local development", "role": "admin"}}
    user = await db.get(RecruiterUserModel, principal.id)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"user": _public_user(user)}


@router.post("/logout")
async def logout(request: Request, response: Response,
                 principal: Principal = Depends(current_principal), db: AsyncSession = Depends(get_db)):
    _same_origin(request)
    user = await db.get(RecruiterUserModel, principal.id)
    if user:
        user.token_version += 1
        await db.commit()
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"status": "signed_out"}
