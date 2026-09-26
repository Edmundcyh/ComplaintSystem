from typing import Annotated

from pydantic import Field, field_validator

from schemas.base import BaseComplaint, Money
from utils.helpers import ALLOWED_PHOTO_EXTENSIONS, MAX_PHOTO_BYTES


class ComplaintIn(BaseComplaint):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=5000)
    amount: Annotated[Money, Field(gt=0, max_digits=10, decimal_places=2)]
    # Generous bound to reject huge bodies early; the decoded size is
    # checked exactly in decode_photo
    encoded_photo: str = Field(max_length=2 * MAX_PHOTO_BYTES)
    extension: str

    @field_validator("extension")
    @classmethod
    def check_extension(cls, value):
        extension = value.lower().lstrip(".")
        if extension not in ALLOWED_PHOTO_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_PHOTO_EXTENSIONS))
            raise ValueError(f"Extension must be one of: {allowed}")
        return extension
