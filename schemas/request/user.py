from typing import Annotated

from pydantic import AfterValidator, Field

from schemas.base import BaseUser
from utils.validators import check_email, check_password_bytes, normalize_iban


class UserRegisterIn(BaseUser):
    email: Annotated[str, Field(max_length=120), AfterValidator(check_email)]
    password: Annotated[str, Field(min_length=8), AfterValidator(check_password_bytes)]
    phone: str = Field(max_length=30)
    first_name: str = Field(min_length=1, max_length=30)
    last_name: str = Field(min_length=1, max_length=30)
    iban: Annotated[str, AfterValidator(normalize_iban)]


class UserLoginIn(BaseUser):
    password: str
