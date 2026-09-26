from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, PlainSerializer

# Stored as an exact decimal, but still sent to clients as a JSON number
Money = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]


class BaseComplaint(BaseModel):
    title: str
    description: str
    amount: Money


class BaseUser(BaseModel):
    email: str
