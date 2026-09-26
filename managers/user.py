import bcrypt
from asyncpg import UniqueViolationError
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from db import database
from managers.auth import AuthManager
from models import user, RoleType


def _password_bytes(password):
    # bcrypt only uses the first 72 bytes. New passwords can't be longer
    # (see check_password_bytes), but passlib, used previously, truncated
    # silently, so accounts created before that still need the truncation.
    return password.encode("utf-8")[:72]


def hash_password(password):
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password, password_hash):
    return bcrypt.checkpw(_password_bytes(password), password_hash.encode("utf-8"))


class UserManager:
    @staticmethod
    async def register(user_data):
        # bcrypt is slow on purpose; keep it off the event loop
        user_data["password"] = await run_in_threadpool(
            hash_password, user_data["password"]
        )
        try:
            id_ = await database.execute(user.insert().values(**user_data))
        except UniqueViolationError:
            raise HTTPException(400, "User with this email already exists")
        user_do = await database.fetch_one(user.select().where(user.c.id == id_))
        return AuthManager.encode_token(user_do)

    @staticmethod
    async def login(user_data):
        user_do = await database.fetch_one(
            user.select().where(user.c.email == user_data["email"])
        )
        if not user_do:
            raise HTTPException(400, "Wrong email or password")
        elif not await run_in_threadpool(
            verify_password, user_data["password"], user_do["password"]
        ):
            raise HTTPException(400, "Wrong email or password")
        return AuthManager.encode_token(user_do), user_do["role"]

    @staticmethod
    async def get_all_users():
        return await database.fetch_all(user.select())

    @staticmethod
    async def get_user_by_email(email):
        return await database.fetch_all(user.select().where(user.c.email == email))

    @staticmethod
    async def change_role(role: RoleType, user_id):
        await database.execute(
            user.update().where(user.c.id == user_id).values(role=role)
        )
