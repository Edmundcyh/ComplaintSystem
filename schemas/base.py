from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

# Stored as an exact decimal, but still sent to clients as a JSON number
Money = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]


class BaseComplaint(BaseModel):
    # Numbers are accepted for text fields, as with pydantic v1
    model_config = ConfigDict(coerce_numbers_to_str=True)

    title: str
    description: str
    amount: Money


class BaseUser(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)

    email: str
