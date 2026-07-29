"""Opt-in local user seed.

Requires ``SEED_EMAIL`` and ``SEED_PASSWORD``. The password is never embedded
in source or printed.
"""
from __future__ import annotations

import os

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import select

from app.core.security import hash_password
from app.database import SessionLocal
from app.models import User
from app.schemas import UserCreate


def seed() -> None:
    email = os.environ.get("SEED_EMAIL", "")
    password = os.environ.get("SEED_PASSWORD", "")
    credentials = UserCreate(
        email=TypeAdapter(EmailStr).validate_python(email),
        password=password,
    )
    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.email == credentials.email))
        if existing is not None:
            print("Seed user already exists.")
            return
        db.add(
            User(
                email=credentials.email,
                hashed_password=hash_password(credentials.password),
            )
        )
        db.commit()
    print("Seed user created.")


if __name__ == "__main__":
    seed()
