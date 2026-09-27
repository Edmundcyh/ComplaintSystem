from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from decouple import config
from fastapi import HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.requests import Request

from db import database
from models import user, RoleType


class AuthManager:
    @staticmethod
    def encode_token(user):
        payload = {
            # PyJWT >= 2.10 requires "sub" to be a string
            "sub": str(user["id"]),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=120),
        }
        return jwt.encode(payload, config("SECRET_KEY"), algorithm="HS256")
        # ES256 https://curity.io/resources/learn/jwt-best-practices/#:~:text=When%20signing%20is%20considered%2C%20currently,v1_5%20using%20SHA%2D256)
        # pip install pyjwt[crypto]


class CustomHTTPBearer(HTTPBearer):
    async def __call__(
        self, request: Request
    ) -> Optional[HTTPAuthorizationCredentials]:
        res = await super().__call__(request)

        try:
            payload = jwt.decode(
                res.credentials,
                config("SECRET_KEY"),
                algorithms=["HS256"],
                options={"require": ["exp", "sub"]},
            )
            user_id = int(payload["sub"])
        except jwt.ExpiredSignatureError:
            raise HTTPException(401, "Token is expired")
        except (jwt.InvalidTokenError, ValueError):
            raise HTTPException(401, "Invalid Token")

        user_data = await database.fetch_one(user.select().where(user.c.id == user_id))
        if not user_data:
            # e.g. the user was deleted after the token was issued
            raise HTTPException(401, "Invalid Token")
        request.state.user = user_data  # same like the User Mixin
        return res


oauth2_scheme = CustomHTTPBearer()


def is_complainer(request: Request):
    if not request.state.user["role"] == RoleType.complainer:
        raise HTTPException(403, "Forbidden")


def is_approver(request: Request):
    if not request.state.user["role"] == RoleType.approver:
        raise HTTPException(403, "Forbidden")


def is_admin(request: Request):
    if not request.state.user["role"] == RoleType.admin:
        raise HTTPException(403, "Forbidden")
