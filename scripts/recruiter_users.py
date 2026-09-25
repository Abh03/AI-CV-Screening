"""Provision local recruiter accounts without exposing passwords in shell history.

Run after `alembic upgrade head`: python -m scripts.recruiter_users create EMAIL USERNAME
"""
import argparse
import asyncio
import getpass
from uuid import uuid4

from sqlalchemy import select

from app.core.auth import hash_password
from app.models.database import AsyncSessionLocal, RecruiterUserModel


async def create_user(email: str, username: str, password: str, role: str = "recruiter") -> str:
    email = email.strip().lower()
    username = username.strip().lower()
    if not email or "@" not in email or len(email) > 255 or not username or len(username) > 64:
        raise ValueError("Enter a valid email and username")
    if role not in {"recruiter", "admin"}:
        raise ValueError("Invalid role")
    async with AsyncSessionLocal() as db:
        existing = (await db.execute(select(RecruiterUserModel).where(
            (RecruiterUserModel.email == email) | (RecruiterUserModel.username == username)))).scalar_one_or_none()
        if existing:
            raise ValueError("Email or username already exists")
        user = RecruiterUserModel(id=str(uuid4()), email=email, username=username,
                                  password_hash=hash_password(password), role=role,
                                  is_active=True, token_version=0, failed_attempts=0)
        db.add(user)
        await db.commit()
        return user.id


async def update_user(identifier: str, *, disable: bool = False, password: str | None = None) -> None:
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(RecruiterUserModel).where(
            (RecruiterUserModel.email == identifier.lower()) |
            (RecruiterUserModel.username == identifier.lower())))).scalar_one_or_none()
        if user is None:
            raise ValueError("User not found")
        if disable:
            user.is_active = False
        if password is not None:
            user.password_hash = hash_password(password)
            user.failed_attempts = 0
            user.locked_until = None
        user.token_version += 1
        await db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create")
    create.add_argument("email")
    create.add_argument("username")
    create.add_argument("--role", choices=["recruiter", "admin"], default="recruiter")
    disable = sub.add_parser("disable")
    disable.add_argument("identifier")
    reset = sub.add_parser("reset-password")
    reset.add_argument("identifier")
    args = parser.parse_args()
    if args.action == "create":
        password = getpass.getpass("Password (at least 12 characters): ")
        print(asyncio.run(create_user(args.email, args.username, password, args.role)))
    elif args.action == "disable":
        asyncio.run(update_user(args.identifier, disable=True))
        print("Account disabled")
    else:
        password = getpass.getpass("New password (at least 12 characters): ")
        asyncio.run(update_user(args.identifier, password=password))
        print("Password changed; existing sessions revoked")


if __name__ == "__main__":
    main()
