import bcrypt
from asyncpg import UniqueViolationError
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from db import database
from managers.auth import AuthManager
from models import user, RoleType

# Checked against when the email is unknown, so a login attempt takes the same
# time whether or not the address is registered
_UNKNOWN_USER_HASH = bcrypt.hashpw(b"unknown user", bcrypt.gensalt()).decode("utf-8")


def _password_bytes(password):
    # bcrypt only uses the first 72 bytes. New passwords can't be longer
    # (see check_password_bytes), but passlib, used previously, truncated
    # silently, so accounts created before that still need the truncation.
    return password.encode("utf-8")[:72]


def hash_password(password):
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password, password_hash):
    try:
        password_bytes = _password_bytes(password)
    except UnicodeEncodeError:
        # Not a password anyone can have registered with
        return False
    return bcrypt.checkpw(password_bytes, password_hash.encode("utf-8"))


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
            user.select().where(user.c.email == user_data["email"].lower())
        )
        password_hash = user_do["password"] if user_do else _UNKNOWN_USER_HASH
        matches = await run_in_threadpool(
            verify_password, user_data["password"], password_hash
        )
        if not user_do or not matches:
            raise HTTPException(400, "Wrong email or password")
        return AuthManager.encode_token(user_do), user_do["role"]

    @staticmethod
    async def get_all_users():
        return await database.fetch_all(user.select())

    @staticmethod
    async def get_user_by_email(email):
        return await database.fetch_all(
            user.select().where(user.c.email == email.lower())
        )

    @staticmethod
    async def change_role(role: RoleType, user_id, admin):
        if user_id == admin["id"]:
            # Otherwise an admin can lock everyone out by demoting themselves
            raise HTTPException(400, "You cannot change your own role")
        async with database.transaction():
            # Both rows are locked (in id order, so two requests can't
            # deadlock) and the admin's role is read again under the lock:
            # of two admins demoting each other at the same time, only the
            # first succeeds, so there is always an admin left
            rows = {}
            for id_ in sorted((admin["id"], user_id)):
                rows[id_] = await database.fetch_one(
                    user.select().where(user.c.id == id_).with_for_update()
                )
            admin_do = rows[admin["id"]]
            if not admin_do or admin_do["role"] != RoleType.admin:
                raise HTTPException(403, "Forbidden")
            if not rows[user_id]:
                raise HTTPException(404, "User not found")
            await database.execute(
                user.update().where(user.c.id == user_id).values(role=role)
            )
